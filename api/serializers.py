from rest_framework import serializers
from .models import Driver, Job, ActionLog, Developer, ApiKey, AppNotification, TeamMember, ApiRequestLog, AudioAsset

class DriverSerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = '__all__'

class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = '__all__'

class ActionLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionLog
        fields = '__all__'

class DeveloperSerializer(serializers.ModelSerializer):
    class Meta:
        model = Developer
        fields = '__all__'

class ApiKeySerializer(serializers.ModelSerializer):
    """`developer` and `key` are set server-side; clients only send `name` on create."""

    request_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ApiKey
        fields = '__all__'
        extra_kwargs = {
            'developer': {'read_only': True},
            'key': {'read_only': True},
        }

    def get_request_count(self, obj):
        return ApiRequestLog.objects.filter(
            developer_id=obj.developer_id,
            api_key_value=obj.key,
        ).count()

class AppNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppNotification
        fields = '__all__'

class TeamMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeamMember
        fields = '__all__'

class ApiRequestLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiRequestLog
        fields = "__all__"

class AudioAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = AudioAsset
        fields = "__all__"

