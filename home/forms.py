# home/forms.py
from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator
# 1. Imported your models directly so the forms can run queries
from .models import Uni, Course 

# Keep static structural definitions intact
SEMESTER_CHOICES = [
    ('1st Semester', '1st Semester'),
    ('2nd Semester', '2nd Semester'),
    ('3rd Semester', '3rd Semester'),
    ('4th Semester', '4th Semester'),
    ('5th Semester', '5th Semester'),
    ('6th Semester', '6th Semester'),
    ('7th Semester', '7th Semester'),
    ('8th Semester', '8th Semester'),
]

TERM_CHOICES = [
    ('Mid Term', 'Mid Term'),
    ('Final Term', 'Final Term'),
]

REPORTED_REASONS = [
    'Paper detail(s) do not match the file',
    'Unable to view paper',
    'File is corrupted',
    'Unable to download paper',
    'Inappropriate content',
    'Button(s) not working',
]

REPORT_CHOICES = [(reason, reason) for reason in REPORTED_REASONS]


class AcademicUploadForm(forms.Form):
    title = forms.CharField(max_length=255, min_length=2, strip=True, required=True)
    year = forms.IntegerField(validators=[MinValueValidator(2000), MaxValueValidator(2030)], required=True)
    session = forms.CharField(max_length=50, strip=True, required=True)
    course_name = forms.CharField(max_length=100, strip=True, required=True)
    semester = forms.ChoiceField(choices=SEMESTER_CHOICES, required=True)
    term = forms.ChoiceField(choices=TERM_CHOICES, required=True)
    
    # 2. Changed university to pull directly from your Uni database table
    university = forms.ModelChoiceField(
        queryset=Uni.objects.all(),
        to_field_name="uni_name",
        empty_label="Select University",
        required=True
    )
    
    # 3. Changed program to start blank and populate dynamically
    program = forms.ChoiceField(choices=[], required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dynamically pulls unique programs currently saved inside your Course records
        distinct_programs = Course.objects.values_list('program', flat=True).distinct().order_by('program')
        self.fields['program'].choices = [('', 'Select Program')] + [(p, p) for p in distinct_programs if p]


class SearchValidationForm(forms.Form):
    q = forms.CharField(max_length=100, required=False, strip=True)
    
    # Restored Uni reference (Fixed NameError)
    university = forms.ModelChoiceField(
        queryset=Uni.objects.all(),
        to_field_name="uni_name",
        empty_label="All Universities",
        required=False
    )
    semester = forms.ChoiceField(choices=[('', 'All Semesters')] + SEMESTER_CHOICES, required=False)
    year = forms.IntegerField(validators=[MinValueValidator(2000), MaxValueValidator(2030)], required=False)
    term = forms.ChoiceField(choices=[('', 'All Terms')] + TERM_CHOICES, required=False)
    course_name = forms.CharField(max_length=255, required=False, strip=True)
    status = forms.ChoiceField(choices=[('', 'All Statuses'), ('Pending', 'Pending'), ('Approved', 'Approved')], required=False)
    
    # 4. Changed program to dynamic lookup
    program = forms.ChoiceField(choices=[], required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dynamically builds dropdown choices from unique entries in your database
        distinct_programs = Course.objects.values_list('program', flat=True).distinct().order_by('program')
        self.fields['program'].choices = [('', 'All Programs')] + [(p, p) for p in distinct_programs if p]

    page = forms.IntegerField(required=False, min_value=1, max_value=999, initial=1)
    def clean_page(self):
        page = self.cleaned_data.get('page')
        if not page:
            return 1
        return page


class ReportPaperForm(forms.Form):
    reported_reason = forms.ChoiceField(
        choices=REPORT_CHOICES,
        error_messages={'invalid_choice': 'Please select a valid reason from the dropdown.'}
    )


class ViewPaperActionForm(forms.Form):
    action = forms.ChoiceField(choices=[('report', 'report'), ('toggle_save', 'toggle_save')])


class LoginRedirectForm(forms.Form):
    next = forms.CharField(required=False, max_length=2048, strip=True)


class ProfileDashboardFilterForm(forms.Form):
    status = forms.ChoiceField(choices=[('', 'All'), ('Approved', 'Approved'), ('Pending', 'Pending')], required=False)
    view = forms.ChoiceField(choices=[('uploaded', 'uploaded'), ('saved', 'saved')], required=False, initial='uploaded')
    page = forms.IntegerField(required=False, min_value=1, max_value=999, initial=1)
    
    def clean_page(self):
        page = self.cleaned_data.get('page')
        if not page:
            return 1
        return page