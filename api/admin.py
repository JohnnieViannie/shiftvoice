from django.contrib import admin
from .models import Driver, Job, ActionLog

@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ('driver_id', 'name', 'phone_number', 'current_status')
    search_fields = ('name', 'driver_id')
    list_filter = ('current_status',)

@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ('job_id', 'status', 'assigned_driver', 'created_at')
    search_fields = ('job_id',)
    list_filter = ('status',)

@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'driver', 'intent_action', 'job_id', 'status')
    search_fields = ('driver__name', 'intent_action', 'voice_command')
    list_filter = ('intent_action', 'status')
