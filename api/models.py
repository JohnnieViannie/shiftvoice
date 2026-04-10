from django.db import models
from django.contrib.auth.models import User
import uuid


class Driver(models.Model):
    STATUS_CHOICES = [
        ('OFFLINE', 'Offline'),
        ('ONLINE', 'Online'),
        ('IN_TRIP', 'In Trip'),
    ]

    driver_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(max_length=255, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    current_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='OFFLINE'
    )

    def __str__(self):
        return f"{self.name} ({self.driver_id})"


class Job(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED', 'Rejected'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    job_id = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    assigned_driver = models.ForeignKey(
        Driver,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Job {self.job_id} - {self.status}"


class ActionLog(models.Model):
    ACTION_CHOICES = [
        ('START_SHIFT', 'Start Shift'),
        ('ACCEPT_JOB', 'Accept Job'),
        ('REJECT_JOB', 'Reject Job'),
        ('CONFIRM_TAXI_NUMBER', 'Confirm Taxi Number'),
        ('UNKNOWN', 'Unknown')
    ]

    timestamp = models.DateTimeField(auto_now_add=True)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE)
    voice_command = models.TextField()
    intent_action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    job_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, default='SUCCESS')
    response_payload = models.JSONField(blank=True)

    def __str__(self):
        return f"{self.driver.driver_id} - {self.intent_action} at {self.timestamp}"


class ApiRequestLog(models.Model):
    REQUEST_TYPE_CHOICES = [
        ("STT", "Voice-to-Text"),
        ("TTS", "Text-to-Voice"),
        ("OTHER", "Other"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)

    developer_id = models.BigIntegerField(blank=True)
    customer_email = models.EmailField(blank=True)
    api_key_value = models.CharField(max_length=100, blank=True)

    request_type = models.CharField(max_length=10, choices=REQUEST_TYPE_CHOICES, default="OTHER")
    method = models.CharField(max_length=10, default="POST")
    endpoint = models.CharField(max_length=255)
    status_code = models.IntegerField()
    latency_ms = models.IntegerField(blank=True)
    ip_address = models.GenericIPAddressField(blank=True)

    provider = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.request_type} {self.status_code} {self.endpoint}"


class AudioAsset(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    developer_id = models.BigIntegerField(blank=True, db_index=True)
    provider = models.CharField(max_length=50, blank=True)
    customer_email = models.EmailField(blank=True)
    request_id = models.CharField(max_length=50, blank=True)

    audio_file = models.FileField(upload_to="audio/")
    content_type = models.CharField(max_length=100, blank=True)
    text = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AudioAsset {self.id} ({self.provider})"


class Developer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=255)
    company_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    preferences = models.JSONField(default=dict, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True)

    def __str__(self):
        return self.company_name


class ApiKey(models.Model):
    developer = models.ForeignKey(Developer, on_delete=models.CASCADE, related_name='api_keys')
    name = models.CharField(max_length=100)
    key = models.CharField(max_length=100, unique=True, default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=[('active', 'Active'), ('revoked', 'Revoked')],
        default='active'
    )

    def __str__(self):
        return f"{self.name} - {self.developer.company_name}"


class AppNotification(models.Model):
    developer = models.ForeignKey(Developer, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    message = models.TextField()
    time = models.CharField(max_length=50)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class TeamMember(models.Model):
    developer = models.ForeignKey(Developer, on_delete=models.CASCADE, related_name='team_members')
    name = models.CharField(max_length=255)
    email = models.EmailField()
    role = models.CharField(max_length=50, default='Member')
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class EmailVerification(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def __str__(self):
        return f"Verification for {self.user.email}"