from django.contrib import admin

from .models import Availability, TrainerAssignment, TrainerCertificate, TrainerProfile


class AvailabilityInline(admin.TabularInline):
    model = Availability
    extra = 0


@admin.register(TrainerProfile)
class TrainerProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "employment_status", "employment_type", "hired_on")
    list_filter = ("employment_status", "employment_type")
    inlines = [AvailabilityInline]
    raw_id_fields = ("user",)


@admin.register(TrainerAssignment)
class TrainerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("trainer", "member", "start_date", "end_date")
    raw_id_fields = ("trainer", "member")


admin.site.register(TrainerCertificate)
