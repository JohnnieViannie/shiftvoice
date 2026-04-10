from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
import requests  # For external API calls to Speech Engine IP
from django.conf import settings
from django.utils import timezone
import time
from .serializers import DriverSerializer, JobSerializer, ActionLogSerializer, DeveloperSerializer, ApiKeySerializer, AppNotificationSerializer, TeamMemberSerializer
from .models import Driver, Job, ActionLog, Developer, ApiKey, AppNotification, TeamMember, EmailVerification, ApiRequestLog, AudioAsset
from .intent_classify import resolve_intent

class DriverViewSet(viewsets.ModelViewSet):
    queryset = Driver.objects.all()
    serializer_class = DriverSerializer

class JobViewSet(viewsets.ModelViewSet):
    queryset = Job.objects.all()
    serializer_class = JobSerializer

class ActionLogViewSet(viewsets.ModelViewSet):
    queryset = ActionLog.objects.all()
    serializer_class = ActionLogSerializer

    @action(detail=False, methods=['post'])
    def process_command(self, request):
        """
        Mock API endpoint for Layer 5 - Command Processor
        Receives payload: { "action": "accept_job", "driver_id": "14071", "job_id": "optional", "voice_command": "..." }
        """
        data = request.data
        intent_action = data.get('action', '').upper()
        driver_id = data.get('driver_id')
        job_id = data.get('job_id')
        voice_command = data.get('voice_command', '')

        try:
            driver = Driver.objects.get(driver_id=driver_id)
        except Driver.DoesNotExist:
            return Response({"error": "Driver not found", "status": "FAILED"}, status=status.HTTP_404_NOT_FOUND)

        # Basic Mock Logic
        action_status = "SUCCESS"
        if intent_action == "ACCEPT_JOB" and job_id:
            try:
                job = Job.objects.get(job_id=job_id)
                job.status = "ACCEPTED"
                job.assigned_driver = driver
                job.save()
            except Job.DoesNotExist:
                action_status = "FAILED - JOB NOT FOUND"
        
        elif intent_action == "START_SHIFT":
            driver.current_status = "ONLINE"
            driver.save()

        # Log the action (Layer 7 - Compliance Logger)
        log = ActionLog.objects.create(
            driver=driver,
            voice_command=voice_command,
            intent_action=intent_action,
            job_id=job_id,
            status=action_status,
            response_payload={"message": f"Action {intent_action} processed for driver {driver_id}"}
        )

        serializer = self.get_serializer(log)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class VoiceHailingViewSet(viewsets.ViewSet):
    """
    Hands-free voice layer for car-hailing / ride-hail integrations (UK road-safety use case).

    Two directions:
    - Customer ride request (text from your platform) → audio for the driver (no screen read).
    - Driver spoken reply (microphone) → text to send back to the customer app.

    Speech backend: OpenAI or Google (SPEECH_PROVIDER in environment).
    """

    def _stt_driver_reply(self, request):
        """
        Driver microphone → text for the customer/rider channel.

        Provide audio either:
        - multipart form: field `audio` (file upload), or
        - JSON: `"audio": "https://.../file.mp3"` (server fetches the URL, then transcribes).

        Response `text` is what you forward to the passenger.
        """
        import os
        from urllib.parse import urlparse

        from django.core.files.uploadedfile import SimpleUploadedFile

        start_time = time.perf_counter()
        endpoint = request.path
        method = request.method
        provider = os.getenv("SPEECH_PROVIDER", "openai").lower()
        customer_email = request.data.get("email") or request.headers.get("X-CUSTOMER-EMAIL")
        api_key_value = request.headers.get("X-API-KEY") or request.data.get("api_key")

        def get_client_ip(req):
            xff = req.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
            return req.META.get("REMOTE_ADDR")

        # Authenticate partner/customer (MVP)
        api_key_obj = None
        developer = None
        if not customer_email:
            # Still log the attempt (below) but return a clear error.
            status_code = status.HTTP_400_BAD_REQUEST
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=None,
                api_key_value=api_key_value if api_key_value else None,
                customer_email=None,
                request_type="STT",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=provider,
                error_message="Missing required field: email",
            )
            return Response({"error": "Missing required field: email", "request_id": f"req_{log.id}"}, status=status_code)

        if not api_key_value:
            status_code = status.HTTP_401_UNAUTHORIZED
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=None,
                api_key_value=None,
                customer_email=customer_email,
                request_type="STT",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=provider,
                error_message="Missing API key",
            )
            return Response({"error": "Unauthorized. Missing API Key.", "request_id": f"req_{log.id}"}, status=status_code)

        api_key_obj = ApiKey.objects.filter(key=api_key_value, status="active").select_related("developer").first()
        if not api_key_obj:
            status_code = status.HTTP_401_UNAUTHORIZED
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=None,
                api_key_value=api_key_value,
                customer_email=customer_email,
                request_type="STT",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=provider,
                error_message="Invalid or revoked API key",
            )
            return Response({"error": "Unauthorized. Invalid API Key.", "request_id": f"req_{log.id}"}, status=status_code)

        developer = api_key_obj.developer
        ApiKey.objects.filter(id=api_key_obj.id).update(last_used=timezone.now())

        _max_audio_bytes = 25 * 1024 * 1024  # Whisper-style cap

        audio_file = request.FILES.get("audio")
        if not audio_file:
            audio_ref = request.data.get("audio")
            if isinstance(audio_ref, str) and audio_ref.strip().lower().startswith(("http://", "https://")):
                url = audio_ref.strip()
                try:
                    r = requests.get(url, timeout=90)
                    r.raise_for_status()
                    content = r.content
                    if not content:
                        raise ValueError("Empty response body from audio URL")
                    if len(content) > _max_audio_bytes:
                        raise ValueError(f"Audio larger than {_max_audio_bytes // (1024 * 1024)}MB")
                    path = urlparse(url).path
                    fname = path.rsplit("/", 1)[-1] if path else "audio.mp3"
                    if not fname or fname == "/":
                        fname = "audio.mp3"
                    if "." not in fname:
                        fname += ".mp3"
                    ct = (r.headers.get("Content-Type") or "").split(";")[0].strip() or "audio/mpeg"
                    audio_file = SimpleUploadedFile(name=fname, content=content, content_type=ct)
                except Exception as e:
                    status_code = status.HTTP_400_BAD_REQUEST
                    latency_ms = int((time.perf_counter() - start_time) * 1000)
                    log = ApiRequestLog.objects.create(
                        developer_id=developer.id if developer else None,
                        api_key_value=api_key_value,
                        customer_email=customer_email,
                        request_type="STT",
                        method=method,
                        endpoint=endpoint,
                        status_code=status_code,
                        latency_ms=latency_ms,
                        ip_address=get_client_ip(request),
                        provider=provider,
                        error_message=f"Could not load audio from URL: {e}",
                    )
                    return Response(
                        {
                            "error": "Could not load audio from URL.",
                            "details": str(e),
                            "request_id": f"req_{log.id}",
                        },
                        status=status_code,
                    )

        if not audio_file:
            status_code = status.HTTP_400_BAD_REQUEST
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=developer.id if developer else None,
                api_key_value=api_key_value,
                customer_email=customer_email,
                request_type="STT",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=provider,
                error_message="No 'audio' file provided.",
            )
            return Response(
                {
                    "error": "No 'audio' file provided.",
                    "hint": "Upload multipart form field 'audio', or send JSON with 'audio' as an http(s) URL to an audio file.",
                    "request_id": f"req_{log.id}",
                },
                status=status_code,
            )
        
        try:
            if provider == "openai":
                import io
                from openai import OpenAI

                client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
                # openai-python expects bytes, io.IOBase, PathLike, or (filename, io.IOBase); not Django UploadedFile.
                audio_ct = getattr(audio_file, "content_type", None) or "audio/mpeg"
                raw_audio = audio_file.read()
                whisper_name = getattr(audio_file, "name", None) or "audio.mp3"
                whisper_file = (whisper_name, io.BytesIO(raw_audio))
                response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=whisper_file,
                )
                transcribed = response.text
                intent_value = resolve_intent(transcribed, "driver")
                status_code = status.HTTP_200_OK
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                log = ApiRequestLog.objects.create(
                    developer_id=developer.id if developer else None,
                    api_key_value=api_key_value,
                    customer_email=customer_email,
                    request_type="STT",
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    ip_address=get_client_ip(request),
                    provider="openai",
                    metadata={
                        "audio_content_type": audio_ct,
                        "intent_value": intent_value,
                    },
                )
                return Response({
                    "status": "success",
                    "text": transcribed,
                    "provider": "openai",
                    "value": intent_value,
                    "request_id": f"req_{log.id}",
                    "flow": "driver_reply_to_customer_text",
                }, status=status_code)
            
            elif provider == "google":
                from google import genai

                client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
                
                audio_bytes = audio_file.read()
                
                # Using Gemini 1.5 Flash (or 2.5 Flash) for audio understanding
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        {"mime_type": audio_file.content_type or "audio/mpeg", "data": audio_bytes},
                        "Transcribe the following audio exactly as spoken."
                    ]
                )
                transcribed = response.text.strip()
                intent_value = resolve_intent(transcribed, "driver")
                status_code = status.HTTP_200_OK
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                log = ApiRequestLog.objects.create(
                    developer_id=developer.id if developer else None,
                    api_key_value=api_key_value,
                    customer_email=customer_email,
                    request_type="STT",
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    ip_address=get_client_ip(request),
                    provider="google",
                    metadata={
                        "audio_content_type": audio_file.content_type,
                        "intent_value": intent_value,
                    },
                )
                return Response({
                    "status": "success",
                    "text": transcribed,
                    "provider": "google",
                    "value": intent_value,
                    "request_id": f"req_{log.id}",
                    "flow": "driver_reply_to_customer_text",
                }, status=status_code)
            
            else:
                status_code = status.HTTP_400_BAD_REQUEST
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                log = ApiRequestLog.objects.create(
                    developer_id=developer.id if developer else None,
                    api_key_value=api_key_value,
                    customer_email=customer_email,
                    request_type="STT",
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    ip_address=get_client_ip(request),
                    provider=provider,
                    error_message="Invalid SPEECH_PROVIDER. Use 'openai' or 'google'.",
                )
                return Response({"error": "Invalid SPEECH_PROVIDER. Use 'openai' or 'google'.", "request_id": f"req_{log.id}"}, status=status_code)
                
        except Exception as e:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=developer.id if developer else None,
                api_key_value=api_key_value,
                customer_email=customer_email,
                request_type="STT",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=provider,
                error_message=str(e),
            )
            return Response(
                {"error": "Failed to transcribe", "details": str(e), "request_id": f"req_{log.id}"}, 
                status=status_code
            )

    @action(detail=False, methods=['post'], url_path='driver-reply/to-customer-text')
    def driver_reply_to_customer_text(self, request):
        return self._stt_driver_reply(request)

    @action(detail=False, methods=['post'], url_path='to-text')
    def speech_to_text_legacy(self, request):
        """Deprecated: use POST .../voice/driver-reply/to-customer-text/"""
        return self._stt_driver_reply(request)

    def _tts_customer_message(self, request):
        """
        Customer / platform message (text) → spoken audio for the driver (hands-free playback).

        JSON body: `text` (message to read aloud), `email` (partner contact id for logs).
        Returns `audio_url` to stream or download.
        """
        import os
        import io
        import uuid
        import time as _time
        from openai import OpenAI
        from gtts import gTTS
        from django.core.files.base import ContentFile
        from django.utils import timezone
        from datetime import timedelta
        
        start_time = _time.perf_counter()
        endpoint = request.path
        method = request.method

        def get_client_ip(req):
            xff = req.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
            return req.META.get("REMOTE_ADDR")
        
        # 1. Extract payload
        text = request.data.get('text', '')
        email = request.data.get('email', '')  # customer email (required)
        api_key_value = request.headers.get("X-API-KEY") or request.data.get("api_key")
        # OpenAI TTS: voice preset + speed (0.25–4.0; lower = slower). Optional per request.
        tts_voice = (request.data.get("voice") or os.getenv("OPENAI_TTS_VOICE", "nova") or "nova").strip()
        tts_speed_raw = request.data.get("speed", os.getenv("OPENAI_TTS_SPEED", "0.9"))
        try:
            tts_speed = float(tts_speed_raw)
        except (TypeError, ValueError):
            tts_speed = 0.9
        tts_speed = max(0.25, min(4.0, tts_speed))
        # https://platform.openai.com/docs/guides/text-to-speech — fixed presets; "nova"/"shimmer" read as female-presenting
        _openai_voices = frozenset(
            {"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"}
        )
        if tts_voice not in _openai_voices:
            tts_voice = "nova"

        if not text:
            status_code = status.HTTP_400_BAD_REQUEST
            latency_ms = int((_time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=None,
                api_key_value=api_key_value if api_key_value else None,
                customer_email=email or None,
                request_type="TTS",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=os.getenv("SPEECH_PROVIDER", "openai").lower(),
                error_message="No 'text' provided.",
            )
            return Response({"error": "No 'text' provided.", "request_id": f"req_{log.id}"}, status=status_code)
            
        if not email:
            status_code = status.HTTP_400_BAD_REQUEST
            latency_ms = int((_time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=None,
                api_key_value=api_key_value if api_key_value else None,
                customer_email=None,
                request_type="TTS",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=os.getenv("SPEECH_PROVIDER", "openai").lower(),
                error_message="Missing required field: email",
            )
            return Response({"error": "Missing required field: email", "request_id": f"req_{log.id}"}, status=status_code)

        # 2. API Key Authentication (customer API key)
        if not api_key_value:
            status_code = status.HTTP_401_UNAUTHORIZED
            latency_ms = int((_time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=None,
                api_key_value=None,
                customer_email=email,
                request_type="TTS",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=os.getenv("SPEECH_PROVIDER", "openai").lower(),
                error_message="Missing API key",
            )
            return Response({"error": "Unauthorized. Missing API Key.", "request_id": f"req_{log.id}"}, status=status_code)

        api_key_obj = ApiKey.objects.filter(key=api_key_value, status="active").select_related("developer", "developer__user").first()
        if not api_key_obj:
            status_code = status.HTTP_401_UNAUTHORIZED
            latency_ms = int((_time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=None,
                api_key_value=api_key_value,
                customer_email=email,
                request_type="TTS",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=os.getenv("SPEECH_PROVIDER", "openai").lower(),
                error_message="Invalid or revoked API key",
            )
            return Response({"error": "Unauthorized. Invalid API Key.", "request_id": f"req_{log.id}"}, status=status_code)

        developer = api_key_obj.developer
        ApiKey.objects.filter(id=api_key_obj.id).update(last_used=timezone.now())

        # 3. Synthesize Audio
        provider = os.getenv("SPEECH_PROVIDER", "openai").lower()
        audio_bytes = None
        
        try:
            if provider == "openai":
                client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
                response = client.audio.speech.create(
                    model="tts-1",
                    voice=tts_voice,
                    input=text,
                    speed=tts_speed,
                )
                audio_bytes = response.read()
                
            elif provider == "google":
                tts = gTTS(text=text, lang='en')
                audio_fp = io.BytesIO()
                tts.write_to_fp(audio_fp)
                audio_fp.seek(0)
                audio_bytes = audio_fp.read()
                
            else:
                status_code = status.HTTP_400_BAD_REQUEST
                latency_ms = int((_time.perf_counter() - start_time) * 1000)
                log = ApiRequestLog.objects.create(
                    developer_id=developer.id if developer else None,
                    api_key_value=api_key_value,
                    customer_email=email,
                    request_type="TTS",
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    ip_address=get_client_ip(request),
                    provider=provider,
                    error_message="Invalid SPEECH_PROVIDER.",
                )
                return Response({"error": "Invalid SPEECH_PROVIDER.", "request_id": f"req_{log.id}"}, status=status_code)
                
            # 4. Intent label for rider message, then log + persist audio
            intent_value = resolve_intent(text, "customer")
            status_code = status.HTTP_200_OK
            latency_ms = int((_time.perf_counter() - start_time) * 1000)
            meta = {"intent_value": intent_value}
            if provider == "openai":
                meta["openai_voice"] = tts_voice
                meta["openai_speed"] = tts_speed
            log = ApiRequestLog.objects.create(
                developer_id=developer.id if developer else None,
                api_key_value=api_key_value,
                customer_email=email,
                request_type="TTS",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=provider,
                metadata=meta,
            )

            filename = f"tts_{uuid.uuid4()}.mp3"
            asset = AudioAsset.objects.create(
                expires_at=timezone.now() + timedelta(hours=24),
                developer_id=developer.id if developer else None,
                provider=provider,
                customer_email=email,
                request_id=f"req_{log.id}",
                audio_file=ContentFile(audio_bytes, name=filename),
                content_type="audio/mpeg",
                text=text,
            )

            log.metadata = {**(log.metadata or {}), "audio_asset_id": asset.id}
            log.save(update_fields=["metadata"])

            audio_url = request.build_absolute_uri(asset.audio_file.url)

            return Response({
                "status": "success",
                "message": "Ride request message synthesized as audio for hands-free driver playback.",
                "value": intent_value,
                "provider": "ShiftVoice",
                "request_id": f"req_{log.id}",
                "audio_url": audio_url,
                "expires_at": asset.expires_at.isoformat(),
                "flow": "customer_message_to_driver_audio",
            }, status=status_code)
                
        except Exception as e:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            latency_ms = int((_time.perf_counter() - start_time) * 1000)
            log = ApiRequestLog.objects.create(
                developer_id=developer.id if developer else None,
                api_key_value=api_key_value,
                customer_email=email,
                request_type="TTS",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ip_address=get_client_ip(request),
                provider=provider,
                error_message=str(e),
            )
            return Response(
                {"error": "Failed to synthesize speech", "details": str(e), "request_id": f"req_{log.id}"}, 
                status=status_code
            )

    @action(detail=False, methods=['post'], url_path='customer-message/to-driver-audio')
    def customer_message_to_driver_audio(self, request):
        return self._tts_customer_message(request)

    @action(detail=False, methods=['post'], url_path='to-audio')
    def text_to_audio_legacy(self, request):
        """Deprecated: use POST .../voice/customer-message/to-driver-audio/"""
        return self._tts_customer_message(request)

# Dashboard Views
from django.contrib.auth.models import User
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken

class RegisterAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data
        email = data.get('email')
        password = data.get('password')
        full_name = data.get('fullName')
        company_name = data.get('companyName')
        phone = data.get('phoneNumber', '')

        if not email or not password:
            return Response({"error": "Email and password are required"}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(username=email).exists():
            return Response({"error": "User already exists"}, status=status.HTTP_400_BAD_REQUEST)

        # Split full name into first and last name
        name_parts = full_name.strip().split()
        first_name = name_parts[0] if name_parts else ''
        last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

        user = User.objects.create_user(
            username=email, 
            email=email, 
            password=password, 
            first_name=first_name,
            last_name=last_name,
            is_active=False
        )
        developer = Developer.objects.create(
            user=user, 
            full_name=full_name, 
            company_name=company_name,
            phone_number=phone if phone else None
        )
        ApiKey.objects.create(developer=developer, name="Default Key")
        return Response({"message": "User registered successfully"}, status=status.HTTP_201_CREATED)

class SendVerificationAPIView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        email = request.data.get('email')
        user = User.objects.filter(username=email).first()
        if not user:
            return Response({"error": "User not found"}, status=status.HTTP_400_BAD_REQUEST)

        # Generate 6-digit code
        import random
        code = ''.join(random.choices('0123456789', k=6))

        # Save or update verification
        from datetime import timedelta
        from django.utils import timezone
        expires = timezone.now() + timedelta(minutes=10)
        verification, created = EmailVerification.objects.get_or_create(
            user=user,
            defaults={'code': code, 'expires_at': expires}
        )
        if not created:
            verification.code = code
            verification.expires_at = expires
            verification.save()

        # Send email
        from django.core.mail import send_mail
        from django.template.loader import render_to_string
        html_message = render_to_string('api/verification_email.html', {'code': code, 'user': user})
        
        # Create personalized subject
        subject = f"Verify your ShiftVoice account"
        if hasattr(user, 'developer') and user.developer.company_name:
            subject = f"Verify your ShiftVoice account - {user.developer.company_name}"
        
        send_mail(
            subject,
            "",  # plain text version can be added later
            "hello@movarashiftvoice.com",
            [email],
            html_message=html_message,
        )

        return Response({"message": "Verification code sent"}, status=status.HTTP_200_OK)

class VerifyAPIView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        email = request.data.get('email')
        code = request.data.get('code')
        user = User.objects.filter(username=email).first()
        if not user:
            return Response({"error": "User not found"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            verification = EmailVerification.objects.get(user=user)
            from django.utils import timezone
            if verification.code != code or verification.expires_at < timezone.now():
                return Response({"error": "Invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            
            # Activate user
            user.is_active = True
            user.save()
            
            # Delete verification
            verification.delete()
            
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }, status=status.HTTP_200_OK)
        except EmailVerification.DoesNotExist:
            return Response({"error": "No verification code found"}, status=status.HTTP_400_BAD_REQUEST)

class GoogleAuthAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        
        if not token:
            return Response({"error": "No token provided"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Verify access_token by calling Google's userinfo endpoint
            import requests
            response = requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {token}'}
            )
            
            if not response.ok:
                return Response({"error": "Invalid Google token"}, status=status.HTTP_400_BAD_REQUEST)
                
            user_info = response.json()
            email = user_info.get('email')
            full_name = user_info.get('name', 'Google User')
            
            if not email:
                 return Response({"error": "Email not found in Google account"}, status=status.HTTP_400_BAD_REQUEST)
            
            user, created = User.objects.get_or_create(username=email, defaults={'email': email})
            
            if created:
                # Split full name into first and last name
                name_parts = full_name.strip().split()
                first_name = name_parts[0] if name_parts else ''
                last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''
                
                user.first_name = first_name
                user.last_name = last_name
                user.set_unusable_password()
                user.save()
                developer = Developer.objects.create(
                    user=user, 
                    full_name=full_name, 
                    company_name="Google Signed Up User"
                )
                ApiKey.objects.create(developer=developer, name="Default Key")
                
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'is_new_user': created
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class ForgotPasswordAPIView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        user = User.objects.filter(email=email).first()
        if not user:
            # Don't reveal if email exists or not for security
            return Response({"message": "If an account with this email exists, a verification code has been sent."}, status=status.HTTP_200_OK)
        
        # Generate 6-digit code
        import random
        code = ''.join(random.choices('0123456789', k=6))
        
        # Save or update password reset verification
        from datetime import timedelta
        from django.utils import timezone
        expires = timezone.now() + timedelta(minutes=10)
        
        # We'll reuse the EmailVerification model but mark it as password reset
        verification, created = EmailVerification.objects.get_or_create(
            user=user,
            defaults={'code': code, 'expires_at': expires}
        )
        if not created:
            verification.code = code
            verification.expires_at = expires
            verification.save()
        
        # Send email
        from django.core.mail import send_mail
        from django.template.loader import render_to_string
        html_message = render_to_string('api/password_reset_email.html', {'code': code, 'user': user})
        
        subject = "Reset your ShiftVoice password"
        if hasattr(user, 'developer') and user.developer.company_name:
            subject = f"Reset your ShiftVoice password - {user.developer.company_name}"
        
        send_mail(
            subject,
            "",
            "hello@movarashiftvoice.com",
            [email],
            html_message=html_message
        )
        
        return Response({"message": "If an account with this email exists, a verification code has been sent."}, status=status.HTTP_200_OK)

class ResetPasswordAPIView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        email = request.data.get('email')
        code = request.data.get('code')
        new_password = request.data.get('newPassword')
        
        if not email or not code or not new_password:
            return Response({"error": "Email, code, and new password are required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if len(new_password) < 8:
            return Response({"error": "Password must be at least 8 characters long"}, status=status.HTTP_400_BAD_REQUEST)
        
        user = User.objects.filter(email=email).first()
        if not user:
            return Response({"error": "Invalid email or code"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            verification = EmailVerification.objects.get(user=user)
            from django.utils import timezone
            if verification.code != code or verification.expires_at < timezone.now():
                return Response({"error": "Invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            
            # Update password
            user.set_password(new_password)
            user.save()
            
            # Delete the verification code
            verification.delete()
            
            return Response({"message": "Password reset successfully"}, status=status.HTTP_200_OK)
        except EmailVerification.DoesNotExist:
            return Response({"error": "Invalid email or code"}, status=status.HTTP_400_BAD_REQUEST)

class DashboardDataView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        developer = Developer.objects.filter(user=request.user).first()
        if not developer:
            return Response({
                "stats": [
                    {"label": "Total Requests", "value": "0", "change": "", "up": True, "icon": "BarChart3"},
                    {"label": "Success Rate", "value": "100.0%", "change": "", "up": True, "icon": "Activity"},
                    {"label": "Avg Latency", "value": "0ms", "change": "", "up": True, "icon": "Clock"},
                    {"label": "Active Members", "value": "0", "change": "", "up": True, "icon": "Zap"},
                ],
                "recentRequests": [],
                "apiKey": "No active key",
                "planName": "Growth",
                "monthlyUsage": 0,
                "monthlyLimit": 100000,
                "rateLimit": "1,000 req/min",
                "dailyData": [{"day": d, "requests": 0} for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]],
                "hourlyData": [{"hour": "00:00", "tts": 0, "stt": 0}],
            })

        keys = ApiKey.objects.filter(developer=developer, status='active')
        members = TeamMember.objects.filter(developer=developer).count()

        logs_qs = ApiRequestLog.objects.filter(developer_id=developer.id)
        total_calls = logs_qs.count()
        error_calls = logs_qs.exclude(status_code=200).count()
        success_rate = 100.0 - (error_calls / total_calls * 100) if total_calls > 0 else 100.0
        
        recent_requests = []
        logs = logs_qs.order_by('-created_at')[:6]
        for log in logs:
            recent_requests.append({
                "id": f"req_{log.id}",
                "endpoint": log.endpoint,
                "status": log.status_code,
                "latency": f"{log.latency_ms}ms" if log.latency_ms is not None else "0ms",
                "time": log.created_at.strftime("%H:%M")
            })
            
        primary_key = keys.first()
        api_key_str = primary_key.key if primary_key else "No active key"

        # STT/TTS counts per hour for the bar chart
        # Format required by frontend: [{ hour: "08:00", tts: 10, stt: 7 }, ...]
        from django.db.models.functions import TruncHour
        from django.db.models import Count
        hourly = (
            logs_qs
            .annotate(h=TruncHour("created_at"))
            .values("h", "request_type")
            .annotate(c=Count("id"))
            .order_by("h")
        )
        buckets = {}
        for row in hourly:
            hour_label = row["h"].strftime("%H:00") if row["h"] else "00:00"
            if hour_label not in buckets:
                buckets[hour_label] = {"hour": hour_label, "tts": 0, "stt": 0}
            if row["request_type"] == "TTS":
                buckets[hour_label]["tts"] += row["c"]
            elif row["request_type"] == "STT":
                buckets[hour_label]["stt"] += row["c"]

        hourly_data = list(buckets.values())

        from django.db.models import Avg
        avg_lat = logs_qs.filter(status_code=200, latency_ms__isnull=False).aggregate(
            v=Avg("latency_ms")
        )["v"]
        avg_latency_str = f"{int(avg_lat)}ms" if avg_lat is not None else "0ms"

        return Response({
            "stats": [
                { "label": "Total Requests", "value": f"{total_calls:,}", "change": "+12.5%", "up": True, "icon": "BarChart3" },
                { "label": "Success Rate", "value": f"{success_rate:.1f}%", "change": "+0.3%", "up": True, "icon": "Activity" },
                { "label": "Avg Latency", "value": avg_latency_str, "change": "-8ms", "up": True, "icon": "Clock" },
                { "label": "Active Members", "value": f"{members:,}", "change": "+1", "up": True, "icon": "Zap" },
            ],
            "recentRequests": recent_requests,
            "apiKey": api_key_str,
            "planName": "Growth",
            "monthlyUsage": total_calls,
            "monthlyLimit": 100000,
            "rateLimit": "1,000 req/min",
            "dailyData": [
              { "day": "Mon", "requests": 0 },
              { "day": "Tue", "requests": 0 },
              { "day": "Wed", "requests": 0 },
              { "day": "Thu", "requests": 0 },
              { "day": "Fri", "requests": 0 },
              { "day": "Sat", "requests": 0 },
              { "day": "Sun", "requests": 0 },
            ] if total_calls == 0 else [
              { "day": "Mon", "requests": 420 },
              { "day": "Tue", "requests": 580 },
              { "day": "Wed", "requests": 710 },
              { "day": "Thu", "requests": 640 },
              { "day": "Fri", "requests": 890 },
              { "day": "Sat", "requests": 320 },
              { "day": "Sun", "requests": 280 },
            ],
            "hourlyData": hourly_data if total_calls > 0 else [
              { "hour": "00:00", "tts": 0, "stt": 0 },
            ]
        })

class ProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        developer = Developer.objects.filter(user=request.user).first()
        if not developer:
            return Response({
                "name": "",
                "email": request.user.email or "",
                "initials": "?",
                "company": "",
                "phone": "",
                "avatar_url": None,
                "is_staff": request.user.is_staff,
                "is_superuser": request.user.is_superuser,
            })
        parts = [p for p in developer.full_name.split() if p]
        initials = "".join([p[0] for p in parts]).upper()[:2] if parts else "XX"
        avatar_url = request.build_absolute_uri(developer.avatar.url) if developer.avatar else None
        return Response({
            "name": developer.full_name,
            "email": request.user.email,
            "initials": initials,
            "company": developer.company_name,
            "phone": developer.phone_number or "",
            "avatar_url": avatar_url,
            "is_staff": request.user.is_staff,
            "is_superuser": request.user.is_superuser,
        })


class ProfileUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        return self._save(request)

    def patch(self, request):
        return self._save(request)

    def _save(self, request):
        user = request.user
        developer = Developer.objects.filter(user=user).first()
        data = request.data
        name = (data.get("name") or data.get("fullName") or "").strip()
        email = (data.get("email") or "").strip()
        company = (data.get("company") or data.get("companyName") or "").strip()
        phone = (data.get("phone") or data.get("phoneNumber") or "").strip()

        if not developer:
            if not name or not company:
                return Response(
                    {
                        "error": "Name and company are required to create your profile.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            name_parts = name.split()
            user.first_name = name_parts[0] if name_parts else ""
            user.last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
            developer = Developer.objects.create(
                user=user,
                full_name=name,
                company_name=company,
                phone_number=phone or None,
            )
            if not ApiKey.objects.filter(developer=developer).exists():
                ApiKey.objects.create(developer=developer, name="Default Key")

        if name:
            name_parts = name.split()
            user.first_name = name_parts[0] if name_parts else ""
            user.last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
            developer.full_name = name
        if company:
            developer.company_name = company
        dev_phone = phone if phone is not None else developer.phone_number
        developer.phone_number = dev_phone or None

        if email and email != user.email:
            if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                return Response({"error": "Email already in use"}, status=status.HTTP_400_BAD_REQUEST)
            user.email = email
            user.username = email

        user.save()
        developer.save(update_fields=["full_name", "company_name", "phone_number"])

        avatar_url = request.build_absolute_uri(developer.avatar.url) if developer.avatar else None
        return Response({
            "message": "Profile updated",
            "name": developer.full_name,
            "email": user.email,
            "company": developer.company_name,
            "phone": developer.phone_number or "",
            "avatar_url": avatar_url,
        })


class ProfileAvatarUploadAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        _max = 2 * 1024 * 1024
        allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}

        developer = Developer.objects.filter(user=request.user).first()
        if not developer:
            return Response(
                {"error": "Complete your profile (name and company) before uploading an avatar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        f = request.FILES.get("avatar")
        if not f:
            return Response(
                {"error": "No file provided. Send multipart form field 'avatar'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if getattr(f, "size", 0) > _max:
            return Response({"error": "Image must be 2MB or smaller."}, status=status.HTTP_400_BAD_REQUEST)
        ct = (f.content_type or "").split(";")[0].strip().lower()
        if ct not in allowed:
            return Response({"error": "Use JPEG, PNG, WebP, or GIF."}, status=status.HTTP_400_BAD_REQUEST)

        if developer.avatar:
            developer.avatar.delete(save=False)
        developer.avatar = f
        developer.save()
        url = request.build_absolute_uri(developer.avatar.url)
        return Response({"message": "Avatar updated", "avatar_url": url})


class ChangePasswordAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        current = request.data.get("current_password")
        new_pw = request.data.get("new_password")
        if not current or not new_pw:
            return Response(
                {"error": "current_password and new_password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(new_pw) < 8:
            return Response(
                {"error": "New password must be at least 8 characters"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = request.user
        if not user.check_password(current):
            return Response(
                {"error": "Current password is incorrect"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(new_pw)
        user.save()
        return Response({"message": "Password updated"})

class CompleteProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        user = request.user
        full_name = request.data.get('fullName', '').strip()
        company_name = request.data.get('companyName', '').strip()
        phone_number = request.data.get('phoneNumber', '').strip()
        
        # Validate required fields
        if not full_name:
            return Response({"error": "Full name is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not company_name:
            return Response({"error": "Company name is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Split full name into first and last name
        name_parts = full_name.split()
        first_name = name_parts[0] if name_parts else ''
        last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''
        
        # Update Django User
        user.first_name = first_name
        user.last_name = last_name
        user.save()
        
        # Update Developer profile
        developer = Developer.objects.filter(user=user).first()
        if not developer:
            developer = Developer.objects.create(
                user=user,
                full_name=full_name,
                company_name=company_name,
                phone_number=phone_number or None,
            )
            if not ApiKey.objects.filter(developer=developer).exists():
                ApiKey.objects.create(developer=developer, name="Default Key")
        else:
            developer.full_name = full_name
            developer.company_name = company_name
            if phone_number:
                developer.phone_number = phone_number
            developer.save()
        
        return Response({
            "message": "Profile completed successfully",
            "user": {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "developer": {
                    "full_name": developer.full_name,
                    "company_name": developer.company_name,
                    "phone_number": developer.phone_number
                }
            }
        }, status=status.HTTP_200_OK)

class DeveloperViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = DeveloperSerializer

    def get_queryset(self):
        return Developer.objects.filter(user=self.request.user)

class ApiKeyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ApiKeySerializer

    def get_queryset(self):
        dev = Developer.objects.filter(user=self.request.user).first()
        if not dev:
            return ApiKey.objects.none()
        return ApiKey.objects.filter(developer=dev)

    def perform_create(self, serializer):
        dev = Developer.objects.filter(user=self.request.user).first()
        if not dev:
            from rest_framework.exceptions import ValidationError

            raise ValidationError(
                {"detail": "Complete your company profile before creating API keys."},
                code="no_developer",
            )
        serializer.save(developer=dev)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        api_key = self.get_object()
        api_key.status = "revoked"
        api_key.save(update_fields=["status"])
        return Response({"status": "revoked"})

    @action(detail=True, methods=["post"])
    def regenerate(self, request, pk=None):
        import uuid
        api_key = self.get_object()
        api_key.key = str(uuid.uuid4())
        api_key.status = "active"
        api_key.save(update_fields=["key", "status"])
        return Response(self.get_serializer(api_key).data)

class AppNotificationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AppNotificationSerializer

    def get_queryset(self):
        dev = Developer.objects.filter(user=self.request.user).first()
        if not dev:
            return AppNotification.objects.none()
        return AppNotification.objects.filter(developer=dev)

class TeamMemberViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = TeamMemberSerializer

    def get_queryset(self):
        dev = Developer.objects.filter(user=self.request.user).first()
        if not dev:
            return TeamMember.objects.none()
        return TeamMember.objects.filter(developer=dev)

    def perform_create(self, serializer):
        dev = Developer.objects.filter(user=self.request.user).first()
        if not dev:
            from rest_framework.exceptions import ValidationError

            raise ValidationError(
                {"detail": "Complete your company profile before adding team members."},
                code="no_developer",
            )
        serializer.save(developer=dev)
