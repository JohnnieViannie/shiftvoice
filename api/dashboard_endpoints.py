from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from datetime import timedelta

from .models import ApiRequestLog, Developer

class IncidentsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    
    def list(self, request): 
        return Response([])
    
    @action(detail=False, methods=['get'])
    def stats(self, request): 
        return Response({"total": 0, "resolved": 0, "pending": 0})
    
    @action(detail=False, methods=['get'], url_path='trigger-words')
    def trigger_words(self, request): 
        return Response([])
    
    @action(detail=False, methods=['get'])
    def recordings(self, request): 
        return Response([])

class EmergencyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def alerts(self, request): 
        return Response([])
    
    @action(detail=False, methods=['get'])
    def stats(self, request): 
        return Response({"active": 0, "resolved": 0})
    
    @action(detail=False, methods=['get'])
    def config(self, request): 
        return Response({"autoDispatch": False, "emergencyContacts": []})

class InsuranceViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def claims(self, request): 
        return Response([])
    
    @action(detail=False, methods=['get'])
    def stats(self, request): 
        return Response({"totalClaims": "0", "pendingProcessing": "0", "approvedAmount": "£0"})
    
    @action(detail=False, methods=['get'])
    def evidence(self, request): 
        return Response([])
    
    @action(detail=False, methods=['get'])
    def insurers(self, request): 
        return Response([])
    
    @action(detail=False, methods=['get'], url_path='premium-score')
    def premium_score(self, request): 
        return Response({"score": "N/A", "change": "No data yet", "up": True})

class ShiftAnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _logs(self, request):
        dev = Developer.objects.filter(user=request.user).first()
        if not dev:
            return ApiRequestLog.objects.none()
        return ApiRequestLog.objects.filter(developer_id=dev.id)

    @action(detail=False, methods=['get'])

    def stats(self, request):
        """Derive headline stats from API request volume (STT+TTS) for this developer."""

        now = timezone.now()
        start = now - timedelta(days=7)

        qs = self._logs(request).filter(created_at__gte=start)

        total = qs.count()
        avg = round(total / 7.0, 1) if total else 0

        by_hour = {}
        by_weekday = dict.fromkeys(range(7), 0)

        for created_at in qs.values_list("created_at", flat=True):
            hour = created_at.hour
            weekday = created_at.weekday()

            by_hour[hour] = by_hour.get(hour, 0) + 1
            by_weekday[weekday] += 1

        peak_hour = max(by_hour, key=by_hour.get, default=None)

        peak_label = (
            f"{peak_hour:02d}:00"
            if peak_hour is not None and by_hour.get(peak_hour)
            else "N/A"
        )

        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        best_i = (
            max(by_weekday, key=by_weekday.get)
            if total and any(by_weekday.values())
            else None
        )

        best_day = (
            days[best_i]
            if best_i is not None and by_weekday.get(best_i, 0) > 0
            else "N/A"
        )

        return Response({
            "avgJobsPerDay": str(avg),
            "peakHour": peak_label,
            "bestDay": best_day,
            "topZone": "—",
            "avgJobsChange": f"{total} requests (7d)" if total else "",
            "peakHourDetail": "Highest API traffic hour" if peak_label != "N/A" else "Not enough data",
            "bestDayDetail": "Busiest weekday by requests" if best_day != "N/A" else "Not enough data",
            "topZoneChange": "Zone data not tracked yet",
        })

    @action(detail=False, methods=['get'])
    def weekly(self, request):
        """Requests per weekday (last 7 days rolling). Chart uses `jobs` as count."""
        now = timezone.now()
        start = now - timedelta(days=7)
        qs = self._logs(request).filter(created_at__gte=start)
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        counts = {d: 0 for d in days}
        for row in qs.values_list("created_at", flat=True):
            counts[days[row.weekday()]] += 1
        return Response([{"day": d, "jobs": counts[d]} for d in days])

    @action(detail=False, methods=['get'])
    def hourly(self, request):
        """Request volume by hour (UTC) for last 24h."""
        now = timezone.now()
        start = now - timedelta(hours=24)
        qs = self._logs(request).filter(created_at__gte=start)
        hourly = [0] * 24
        for row in qs.values_list("created_at", flat=True):
            hourly[row.hour] += 1
        return Response([{"hour": f"{h:02d}:00", "demand": hourly[h]} for h in range(24)])

    @action(detail=False, methods=['get'], url_path='peak-times')
    def peak_times(self, request):
        now = timezone.now()
        start = now - timedelta(days=7)
        qs = self._logs(request).filter(created_at__gte=start)
        by_hour = {}
        for row in qs.values_list("created_at", flat=True):
            by_hour[row.hour] = by_hour.get(row.hour, 0) + 1
        top = sorted(by_hour.items(), key=lambda x: -x[1])[:5]
        return Response([{"hour": f"{h:02d}:00", "demand": c} for h, c in top])

    @action(detail=False, methods=['get'])
    def recommendation(self, request):
        total = self._logs(request).filter(created_at__gte=timezone.now() - timedelta(days=7)).count()
        msg = (
            "Your API volume is growing — keep monitoring peak hours in the Overview tab."
            if total >= 10
            else "Send a few more STT/TTS requests to see stronger patterns here."
        )
        return Response({"message": msg})

class LogsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    def list(self, request): 
        developer = Developer.objects.filter(user=request.user).first()
        if not developer:
            return Response([])
        logs = ApiRequestLog.objects.filter(developer_id=developer.id).order_by("-created_at")[:200]

        payload = []
        for log in logs:
            payload.append({
                "id": f"req_{log.id}",
                "method": log.method,
                "endpoint": log.endpoint,
                "status": log.status_code,
                "latency": f"{log.latency_ms}ms" if log.latency_ms is not None else "0ms",
                "ip": log.ip_address or "",
                "time": log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            })

        return Response(payload)

def _settings_developer(request):
    return Developer.objects.filter(user=request.user).first()


def _default_notification_prefs():
    return {
        "email": True,
        "usage": True,
        "errors": True,
        "newsletter": False,
        "slack": False,
    }


class SettingsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def notifications(self, request):
        dev = _settings_developer(request)
        if not dev:
            return Response(_default_notification_prefs())
        prefs = dev.preferences or {}
        stored = prefs.get("notifications") or {}
        out = _default_notification_prefs()
        for k in out:
            out[k] = bool(stored.get(k, out[k]))
        return Response(out)

    @action(detail=False, methods=["put", "patch"], url_path="notifications/update")
    def update_notifications(self, request):
        from rest_framework.exceptions import ValidationError

        dev = _settings_developer(request)
        if not dev:
            raise ValidationError({"detail": "Complete your profile first."})
        body = request.data if isinstance(request.data, dict) else {}
        cur = dict(dev.preferences or {})
        n = dict(cur.get("notifications") or {})
        defaults = _default_notification_prefs()
        for k in defaults:
            if k in body:
                n[k] = bool(body[k])
        merged = {**defaults, **n}
        cur["notifications"] = merged
        dev.preferences = cur
        dev.save(update_fields=["preferences"])
        return Response(merged)

    @action(detail=False, methods=["get"])
    def webhooks(self, request):
        dev = _settings_developer(request)
        if not dev:
            return Response({"url": "", "events": []})
        w = (dev.preferences or {}).get("webhook") or {}
        return Response({"url": w.get("url") or "", "events": w.get("events") or []})

    @action(detail=False, methods=["put", "patch"], url_path="webhooks/update")
    def update_webhooks(self, request):
        from rest_framework.exceptions import ValidationError

        dev = _settings_developer(request)
        if not dev:
            raise ValidationError({"detail": "Complete your profile first."})
        url = request.data.get("url", "")
        events = request.data.get("events", [])
        if not isinstance(events, list):
            events = []
        cur = dict(dev.preferences or {})
        cur["webhook"] = {"url": (url or "")[:2000], "events": events}
        dev.preferences = cur
        dev.save(update_fields=["preferences"])
        return Response(cur["webhook"])
