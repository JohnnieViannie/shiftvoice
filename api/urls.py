from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DriverViewSet, JobViewSet, ActionLogViewSet, VoiceHailingViewSet,
    DeveloperViewSet, ApiKeyViewSet, AppNotificationViewSet, TeamMemberViewSet,
    RegisterAPIView, DashboardDataView, GoogleAuthAPIView, SendVerificationAPIView, VerifyAPIView, ProfileAPIView, ProfileUpdateAPIView, ProfileAvatarUploadAPIView, ChangePasswordAPIView, CompleteProfileAPIView, ForgotPasswordAPIView, ResetPasswordAPIView
)
from .dashboard_endpoints import (
    IncidentsViewSet, EmergencyViewSet, InsuranceViewSet, 
    ShiftAnalyticsViewSet, LogsViewSet, SettingsViewSet
)
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

router = DefaultRouter()
router.register(r'drivers', DriverViewSet)
router.register(r'jobs', JobViewSet)
router.register(r'logs', ActionLogViewSet)
# Hands-free car-hailing voice (customer text→driver audio, driver audio→customer text)
router.register(r'voice', VoiceHailingViewSet, basename='voice')


router.register(r'developers', DeveloperViewSet, basename='developer')
router.register(r'api-keys', ApiKeyViewSet, basename='apikey')
router.register(r'notifications', AppNotificationViewSet, basename='appnotification')
router.register(r'team', TeamMemberViewSet, basename='teammember')

# Dashboard Zero-State Mock endpoints
router.register(r'incidents', IncidentsViewSet, basename='incidents')
router.register(r'emergency', EmergencyViewSet, basename='emergency')
router.register(r'insurance', InsuranceViewSet, basename='insurance')
router.register(r'shift-analytics', ShiftAnalyticsViewSet, basename='shift-analytics')
router.register(r'dashboard-logs', LogsViewSet, basename='dashboard-logs')
router.register(r'settings', SettingsViewSet, basename='settings')

urlpatterns = [
    path('auth/register/', RegisterAPIView.as_view(), name='register'),
    path('auth/send-verification/', SendVerificationAPIView.as_view(), name='send-verification'),
    path('auth/verify/', VerifyAPIView.as_view(), name='verify'),
    path('auth/forgot-password/', ForgotPasswordAPIView.as_view(), name='forgot-password'),
    path('auth/reset-password/', ResetPasswordAPIView.as_view(), name='reset-password'),
    path('auth/login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/google/callback/', GoogleAuthAPIView.as_view(), name='google_auth'),
    path('profile/', ProfileAPIView.as_view(), name='profile-data'),
    path('profile/update/', ProfileUpdateAPIView.as_view(), name='profile-update'),
    path('profile/change-password/', ChangePasswordAPIView.as_view(), name='profile-change-password'),
    path('profile/complete/', CompleteProfileAPIView.as_view(), name='complete-profile'),
    path('profile/upload-avatar/', ProfileAvatarUploadAPIView.as_view(), name='profile-upload-avatar'),
    path('dashboard/stats/', DashboardDataView.as_view(), name='dashboard-data'),
    path('', include(router.urls)),
]
