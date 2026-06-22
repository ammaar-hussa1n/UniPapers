from django.contrib import admin
from allauth.account.admin import EmailAddressAdmin as AllauthEmailAddressAdmin
from allauth.account.models import EmailAddress
from .models import Uni, Record, ReportedRecord, Report, Course

admin.ModelAdmin.list_per_page = 10

def staff_status(self, obj):
    return 'Admin' if obj.user and obj.user.is_staff else 'Not Admin'

AllauthEmailAddressAdmin.list_per_page = 10
AllauthEmailAddressAdmin.list_display = ('email', 'user', 'staff_status', 'verified', 'primary')
AllauthEmailAddressAdmin.search_fields = ('email', 'user__email', 'user__username', 'user__first_name', 'user__last_name')
AllauthEmailAddressAdmin.list_filter = ('verified', 'primary', 'user__is_staff')
AllauthEmailAddressAdmin.ordering = ('email',)
AllauthEmailAddressAdmin.staff_status = staff_status
AllauthEmailAddressAdmin.staff_status.short_description = 'Staff Status'

class CourseInline(admin.TabularInline):
    model = Course
    extra = 1
    fields = ('program', 'semester', 'course_name', 'year', 'term', 'session')

@admin.register(Uni)
class UniAdmin(admin.ModelAdmin):
    list_display = ('id', 'uni_name')
    search_fields = ('uni_name',)

    inlines = [CourseInline]

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('course_name', 'uni', 'program', 'semester', 'year', 'term')
    list_filter = ('uni', 'program', 'semester', 'year', 'term')
    search_fields = ('course_name', 'program', 'semester')

class DynamicReportMixin:
    """
    Forces the 'msg' text field on the edit form to contain all strings from 
    the related Report table, and deletes backend Report rows when cleared.
    """
    def get_form(self, request, obj=None, **kwargs):
        if obj and obj.reports.exists():
            compiled_messages = [r.message for r in obj.reports.all()]

            obj.msg = "\n".join(compiled_messages)
            
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        submitted_msg = form.cleaned_data.get('msg', '')

        if change and not submitted_msg:
            obj.reports.all().delete()
            obj.msg = ""
        else:
            obj.msg = submitted_msg
            
        super().save_model(request, obj, form, change)

@admin.register(Record)
class RecordAdmin(DynamicReportMixin, admin.ModelAdmin):
    list_per_page = 10
    list_select_related = ('course', 'course__uni')
    
    list_display = ('title', 'get_uni', 'get_program', 'get_semester', 'get_course', 'get_term', 'status', 'report_message', 'uploaded_at', 'uploaded_by', 'uploaded_email')
    list_editable = ('status',)
    
    list_filter = ('status', 'course__uni__uni_name', 'course__program', 'course__semester', 'course__term')
    search_fields = ('title', 'course__uni__uni_name', 'course__program', 'course__semester', 'course__course_name', 'course__year', 'course__term', 'course__session', 'status', 'uploaded_by', 'uploaded_email', 'reports__message')
    ordering = ('-uploaded_at',)

    @admin.display(ordering='course__uni__uni_name', description='University')
    def get_uni(self, obj):
        return obj.course.uni.uni_name if obj.course and obj.course.uni_id else '-'

    @admin.display(ordering='course__program', description='Program')
    def get_program(self, obj):
        return obj.course.program if obj.course else '-'

    @admin.display(ordering='course__semester', description='Semester')
    def get_semester(self, obj):
        return obj.course.semester if obj.course else '-'

    @admin.display(ordering='course__course_name', description='Course')
    def get_course(self, obj):
        return obj.course.course_name if obj.course else '-'

    @admin.display(ordering='course__term', description='Term')
    def get_term(self, obj):
        return obj.course.term if obj.course else '-'

    @admin.display(description='Report')
    def report_message(self, obj):
        latest_report = obj.reports.first()
        if latest_report:
            return latest_report.message[:80]
        return '-'

@admin.register(ReportedRecord)
class ReportedRecordAdmin(DynamicReportMixin, admin.ModelAdmin):
    list_per_page = 10
    list_select_related = ('course', 'course__uni')

    list_display = ('title', 'get_uni', 'get_course', 'status', 'total_reports', 'report_message')
    list_filter = ('status',)
    search_fields = ('title', 'reports__message')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(reports__isnull=False).distinct()

    @admin.display(ordering='course__uni__uni_name', description='University')
    def get_uni(self, obj):
        return obj.course.uni.uni_name if obj.course and obj.course.uni_id else '-'

    @admin.display(ordering='course__course_name', description='Course')
    def get_course(self, obj):
        return obj.course.course_name if obj.course else '-'

    @admin.display(description='Total Reports')
    def total_reports(self, obj):
        return obj.reports.count()

    @admin.display(description='Report Reason')
    def report_message(self, obj):
        latest_report = obj.reports.first()
        if latest_report:
            return latest_report.message[:80]
        return '-'