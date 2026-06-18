from pathlib import Path
import re
import html
import os
from django.conf import settings
import uuid
import mimetypes
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.contrib.auth.decorators import login_required
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.contrib.auth import logout
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.db import transaction
from django.db.models.functions import Lower, Trim
from django.core.files.storage import default_storage
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django_ratelimit.decorators import ratelimit
from .models import *
from PIL import Image
import requests
import io
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.exceptions import ValidationError
from django.utils.html import strip_tags
from home.catalog import AVAILABLE_UNIVERSITIES, AVAILABLE_PROGRAMS, AVAILABLE_COURSES
from .forms import *

try:
    import magic #put it in REQUIRMENTS.TXT python-magic==0.4.27
except ImportError:
    magic = None

#Update AVAILABLE_COURSES , AVAILABLE_PROGRAMS, and AVAILABLE_UNIVERSITIES in FORMS.PY also


# These lists feed the search filters in the template.
SEARCH_UNIVERSITY_FILTERS = list(AVAILABLE_UNIVERSITIES.values())

SEARCH_SEMESTER_FILTERS = [
    {'label': '1st', 'value': '1st Semester'},
    {'label': '2nd', 'value': '2nd Semester'},
    {'label': '3rd', 'value': '3rd Semester'},
    {'label': '4th', 'value': '4th Semester'},
    {'label': '5th', 'value': '5th Semester'},
    {'label': '6th', 'value': '6th Semester'},
    {'label': '7th', 'value': '7th Semester'},
    {'label': '8th', 'value': '8th Semester'},
]

SEARCH_YEAR_FILTERS = [
    {'label': str(year), 'value': str(year)}
    for year in range(2000, 2027)
]

SEARCH_TERM_FILTERS = [
    {'label': 'Mid Term', 'value': 'Mid Term'},
    {'label': 'Final Term', 'value': 'Final Term'},
]

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_UPLOAD_MIME_TYPES = {
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/webp',
    'image/bmp',
}

IMAGE_UPLOAD_MIME_TYPES = {
    'image/png',
    'image/jpeg',
    'image/webp',
    'image/bmp',
}

MULTI_IMAGE_FILE_PATTERN = re.compile(r'^(upload_[a-f0-9]+_)\d+_.+$')

REPORT_REASON_CHOICES = [
    'Paper detail(s) do not match the file',
    'Unable to view paper',
    'File is corrupted',
    'Unable to download paper',
    'Inappropriate content',
    'Button(s) not working',
]

# errors #

def error_404_view(request, exception):
    context = {
        'status_code': 404,
        'title': 'Page Not Found',
        'description': "We searched everywhere but couldn't find this past paper or page. It might have been removed or renamed."
    }
    return render(request, 'home/errors.html', context, status=404)

def error_500_view(request):
    context = {
        'status_code': 500,
        'title': 'Server Error',
        'description': 'Something went wrong on our end. Please try again later or contact the developer if the issue persists.'
    }
    return render(request, 'home/errors.html', context, status=500)

def error_403_view(request, exception=None):
    context = {
        'status_code': 403,
        'title': 'Access Denied',
        'description': "You don't have permission to view this resource or past paper until it is approved."
    }
    return render(request, 'home/errors.html', context, status=403)

def error_400_view(request, exception=None):
    context = {
        'status_code': 400,
        'title': 'Bad Request',
        'description': 'Your browser sent a request that this server could not understand or process.'
    }
    return render(request, 'home/errors.html', context, status=400)

# errors #

def _clean_text_input(value):
    if value is None:
        return ''
    value = strip_tags(value)
    if isinstance(value, str):
        return value.strip()
    return value

def _normalize_university_input(value):
    value = _clean_text_input(value)
    if not value:
        return ''

    for full_name, university_data in AVAILABLE_UNIVERSITIES.items():
        candidate_values = {
            full_name.casefold(),
            university_data['label'].casefold(),
            university_data['value'].casefold(),
        }
        if value.casefold() in candidate_values:
            return full_name

    return value

def _get_or_create_normalized_uni(university_name):
    university_name = _normalize_university_input(university_name)
    if not university_name:
        return None

    uni = (
        Uni.objects.annotate(normalized_name=Lower(Trim('uni_name')))
        .filter(normalized_name=university_name.casefold())
        .order_by('id')
        .first()
    )
    if uni:
        return uni

    return Uni.objects.create(uni_name=university_name)

def _get_or_create_normalized_course(uni, semester, program, course_name, year, term, session):
    semester = _clean_text_input(semester)
    program = _clean_text_input(program)
    course_name = _clean_text_input(course_name)
    # 1. Convert incoming year string into a clean Python integer
    try:
        year_int = int(str(year).strip())
    except (ValueError, TypeError):
        year_int = 2026 # Fallback default if parsing fails

    term = _clean_text_input(term)
    session = _clean_text_input(session)

    course = (
        Course.objects.annotate(
            normalized_semester=Lower(Trim('semester')),
            normalized_program=Lower(Trim('program')),
            normalized_course_name=Lower(Trim('course_name')),
            # 2. REMOVED Lower(Trim()) from here because database column 'year' is an Integer!
            normalized_term=Lower(Trim('term')),
            normalized_session=Lower(Trim('session')),
        )
        .filter(
            uni=uni,
            normalized_semester=semester.casefold(),
            normalized_program=program.casefold(),
            normalized_course_name=course_name.casefold(),
            year=year_int,  # 3. Match the clean integer directly
            normalized_term=term.casefold(),
            normalized_session=session.casefold(),
        )
        .order_by('id')
        .first()
    )
    if course:
        return course

    return Course.objects.create(
        uni=uni,
        semester=semester,
        program=program,
        course_name=course_name,
        year=year_int,  # 4. Save clean integer
        term=term,
        session=session,
    )

def _detect_uploaded_file_mime(uploaded_file):
    uploaded_file.seek(0)
    initial_bytes = uploaded_file.read(2048)
    uploaded_file.seek(0)

    if magic is None:
        return None

    return magic.from_buffer(initial_bytes, mime=True)

def _validate_uploaded_file(uploaded_file):
    """Reject oversized, unexpected, or spoofed uploads using binary inspection."""
    if uploaded_file.size > MAX_UPLOAD_SIZE:
        return 'File size must be 10 MB or smaller.'

    file_ext = Path(uploaded_file.name).suffix.lower()
    allowed_extensions = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp'}
    if file_ext not in allowed_extensions:
        return 'Unsupported file type!'

    detected_mime = _detect_uploaded_file_mime(uploaded_file)

    if detected_mime and (detected_mime not in ALLOWED_UPLOAD_MIME_TYPES):
        return 'Unsupported file type.'

    return None

def _is_image_uploaded_file(uploaded_file):
    if uploaded_file.name.lower().endswith('.pdf'):
        return False

    detected_mime = _detect_uploaded_file_mime(uploaded_file)
    
    # 1. If magic worked perfectly, validate against your strict list
    if detected_mime is not None:
        return detected_mime in IMAGE_UPLOAD_MIME_TYPES

    # 2. SOFT CHECK BYPASS: If magic failed (returned None), use Django's native check!
    django_mime = getattr(uploaded_file, 'content_type', '').lower()
    return django_mime in IMAGE_UPLOAD_MIME_TYPES or django_mime.startswith('image/')

def _build_multi_image_storage_name(batch_id, index, original_name):
    return f'upload_{batch_id}_{index}{Path(original_name).suffix.lower()}'

def _get_multi_image_attachment_paths(record):
    if not record.file:
        return []

    file_name = Path(record.file.name).name
    match = re.match(r'^upload_([a-f0-9]+)_([1-3])(\.[^.]+)?$', file_name)
    if not match:
        return []

    batch_id = match.group(1)
    directory = Path(record.file.name).parent.as_posix()
    image_extensions = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
    attachment_paths = []

    for index in range(1, 4):
        for extension in image_extensions:
            candidate_path = f'{directory}/upload_{batch_id}_{index}{extension}'
            if default_storage.exists(candidate_path):
                attachment_paths.append(candidate_path)
                break

    return attachment_paths

def _get_multi_image_attachments(record):
    """
    Fetches all associated image files for a specific record.
    Returns a structured list of dictionaries matching the frontend template contract.
    """
    attachments_data = []

    # 1. ONLY append the primary record file if it is NOT a PDF
    if record.file and getattr(record, 'file_extension', '') != '.pdf':
        attachments_data.append({
            'path': record.file.name,
            'url': record.file.url,
            'name': Path(record.file.name).name,
            'title': record.title,
        })

    # 2. Query the secondary pages from your relation table
    secondary_attachments = record.attachments.all()
    
    for attachment in secondary_attachments:
        if attachment.file:
            attachments_data.append({
                'path': attachment.file.name,
                'url': attachment.file.url,
                'name': Path(attachment.file.name).name,
                'title': record.title,
            })

    return attachments_data

def _delete_multi_image_attachments(record):
    for attachment_path in _get_multi_image_attachment_paths(record):
        default_storage.delete(attachment_path)

def _build_year_filters():
    """Return stored years if they exist, otherwise fall back to the default year list."""
    years = list(
        Record.objects.values_list('course__year', flat=True)
        .order_by('course__year')
        .distinct()
    )
    years = [year for year in years if year]
    if not years:
        return SEARCH_YEAR_FILTERS
    return [{'label': year, 'value': year} for year in years]

def _normalize_university(value):
    """Convert short university labels into the stored database values."""
    for full_name, university_data in AVAILABLE_UNIVERSITIES.items():
        if value == full_name or value == university_data['label']:
            return full_name
    return value

def _build_course_filters(university, semester, program):
    """Return the course names available for the selected university, semester, and program."""
    if not (university and semester and program):
        return []

    university_courses = AVAILABLE_COURSES.get(university, {})
    course_key = f'{program}|{semester}'
    return university_courses.get(course_key, [])

def _build_compact_page_window(paginator, current_page_number, window_size=9):
    """Return a compact page window and edge visibility flags for pagination UI."""
    total_pages = paginator.num_pages
    if total_pages <= window_size:
        page_numbers = range(1, total_pages + 1)
        window_start = 1
        window_end = total_pages
    else:
        window_start = current_page_number - 4
        window_end = window_start + window_size - 1

        if window_start < 1:
            window_start = 1
            window_end = window_size
        if window_end > total_pages:
            window_end = total_pages
            window_start = total_pages - window_size + 1

        page_numbers = range(window_start, window_end + 1)

    return {
        'page_numbers': page_numbers,
        'show_first_page': window_start > 1,
        'show_last_page': window_end < total_pages,
        'show_leading_ellipsis': window_start > 2,
        'show_trailing_ellipsis': window_end < total_pages - 1,
        'first_page_number': 1,
        'last_page_number': total_pages,
    }

def _prepare_record_preview(record):
    """Attach preview flags and excerpt text used across paper cards and the detail page."""
    # Use our newly added database field instead of guessing from record.file.name
    file_ext = record.file_extension if record.file_extension else ''
    
    record.file_ext = file_ext
    record.is_pdf = file_ext == '.pdf'
    record.is_image = file_ext in {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
    record.preview_url = reverse('paper_preview', args=[record.id, slugify(record.title)])
    return record

def _attach_preview_metadata(records):
    """Add lightweight preview fields used by search and profile card thumbnails."""
    for record in records:
        _prepare_record_preview(record)
    return records

@ratelimit(key='user_or_ip', rate='50/m', block=True)
def home(request):
    """Render the landing page."""
    return render(request, 'home/home.html')

@ratelimit(key='user_or_ip', rate='50/m', block=True)
def search(request):
    """Show all papers that match the current search filters."""
    form = SearchValidationForm(request.GET)
    if not form.is_valid():
            # If an unexpected field shape or extreme length is injected, drop it cleanly
            messages.error(request, 'Invalid search parameters!')
            return redirect('home')

    search_text = _clean_text_input(form.cleaned_data.get('q'))
    university = _normalize_university_input(form.cleaned_data.get('university'))
    semester = _clean_text_input(form.cleaned_data.get('semester'))
    program = _clean_text_input(form.cleaned_data.get('program'))
    year = _clean_text_input(form.cleaned_data.get('year'))
    term = _clean_text_input(form.cleaned_data.get('term'))
    course_name = _clean_text_input(form.cleaned_data.get('course_name'))
    status = _clean_text_input(form.cleaned_data.get('status'))
    
    normalized_university = _normalize_university(university)
    available_courses = _build_course_filters(normalized_university, semester, program)
    is_admin = request.user.is_superuser

    allowed_statuses = {option['value'] for option in (
        {'label': 'Pending', 'value': 'Pending'},
        {'label': 'Approved', 'value': 'Approved'},
    )}
    if status not in allowed_statuses:
        status = ''

    allowed_years = {option['value'] for option in SEARCH_YEAR_FILTERS}
    if year and year not in allowed_years:
        year = ''

    records = (
        Record.objects.select_related('course__uni').all()
        if is_admin
        else Record.objects.select_related('course__uni').filter(status='Approved')
    )

    if is_admin and status:
        records = records.filter(status__iexact=status)
    if search_text:
        records = records.filter(Q(title__icontains=search_text) | Q(course__course_name__icontains=search_text))
    if normalized_university:
        records = records.filter(course__uni__uni_name__iexact=normalized_university)
    if semester:
        records = records.filter(course__semester__iexact=semester)
    if program:
        records = records.filter(course__program__iexact=program)
    if year:
        records = records.filter(course__year__iexact=year)
    if term:
        records = records.filter(course__term__iexact=term)
    if course_name:
        records = records.filter(course__course_name__iexact=course_name)

    records = records.order_by('-id')

    total_count = records.count()
    paginator = Paginator(records, 10)

    if not form.is_valid():
        # If they inject an integer larger than 9999 or trash text, the form catches it
        page_number = 1
    else:
        page_number = form.cleaned_data.get('page') or 1
    records = paginator.get_page(page_number)
    
    _attach_preview_metadata(records)

    pagination = _build_compact_page_window(paginator, records.number)

    search_query_parts = [university, semester, program, year, term, course_name, search_text]
    if is_admin and status:
        search_query_parts.append(status)
    search_query = ' '.join(filter(None, search_query_parts)).strip()

    query_params = request.GET.copy()
    query_params.pop('page', None)
    page_query_string = query_params.urlencode()

    return render(request, 'home/search.html', {
        'records': records,
        'total_count': total_count,
        'search_query': search_query,
        'selected_university': normalized_university,
        'selected_semester': semester,
        'selected_term': term,
        'selected_program': program,
        'selected_year': year,
        'selected_search_text': search_text,
        'selected_course_name': course_name,
        'selected_status': status,
        'user_is_admin': is_admin,
        'page_query_string': page_query_string,
        **pagination,
        'available_universities': SEARCH_UNIVERSITY_FILTERS,
        'available_semesters': SEARCH_SEMESTER_FILTERS,
        'available_programs': list(
            value for value in Record.objects.values_list('course__program', flat=True).order_by('course__program').distinct() if value
        ),
        'available_courses': available_courses,
        'available_years': _build_year_filters(),
        'available_terms': SEARCH_TERM_FILTERS,
        'available_statuses': [
            {'label': 'Pending', 'value': 'Pending'},
            {'label': 'Approved', 'value': 'Approved'},
        ],
    })

@ratelimit(key='user_or_ip', rate='100/m', block=True)
def view(request, paper_id, paper_title=None):
    is_admin = request.user.is_superuser 

    try:
        record = Record.objects.select_related('course__uni').get(id=paper_id)
    except Record.DoesNotExist:
        messages.error(request, 'Past Paper does not exist!')
        return redirect('home')

    is_owner = request.user.is_authenticated and record.uploaded_email == request.user.email

    if record.status != 'Approved' and not (is_admin or is_owner):
        messages.error(request, 'This paper is not approved!')
        return redirect('home')

    action = None
    if request.method == 'POST':
        action_form = ViewPaperActionForm(request.POST)
        if not action_form.is_valid():
            return HttpResponseBadRequest("Invalid Action")

        action = action_form.cleaned_data.get('action')
        if action not in {'report', 'toggle_save'}:
            return HttpResponseBadRequest("Invalid Action")

    if request.method == 'POST' and action == 'report':
        form = ReportPaperForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Invalid Action!!")
            return redirect('view_paper', paper_id=record.id, paper_title=slugify(record.title))
        
        reported_reason = form.cleaned_data.get('reported_reason')

        if reported_reason not in REPORT_REASON_CHOICES:
            messages.error(request, 'Please select a valid report reason!')
            return redirect('view_paper', paper_id=record.id, paper_title=slugify(record.title))
    
    ###########Correct##### no error##### dont UNDO now  safety check point WO HOO no error only security now
                ##3######5232##oA###i 8812 #no 
                #n doo not no error safety checko CHECK post p p p p p p p p p p p p p p pp p 

        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to report a paper!')
            return redirect('view_paper', paper_id=record.id, paper_title=slugify(record.title))

        report_line = f'{reported_reason} - reported by {request.user.get_username()}'

        existing_reports = Report.objects.filter(record=record)
        if existing_reports.filter(user=request.user, message=report_line).exists():
            messages.error(request, 'You have already reported this paper!')
            return redirect('view_paper', paper_id=record.id, paper_title=slugify(record.title))

        user_report_count = existing_reports.filter(user=request.user).count()
        if user_report_count >= 3:
            messages.error(request, 'Maximum reports reached for this paper!')
            return redirect('view_paper', paper_id=record.id, paper_title=slugify(record.title))

        Report.objects.create(
            record=record,
            user=request.user,
            message=report_line
        )

        messages.success(request, 'Report has been submitted for admin review!')
        return redirect('view_paper', paper_id=record.id, paper_title=slugify(record.title))

    correct_slug = slugify(record.title)
    if paper_title is None or correct_slug != paper_title:
        raise Http404('Past Paper does not exist!')

    if request.method == 'POST' and action == 'toggle_save':
        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to save papers!')
            return redirect('view_paper', paper_id=record.id, paper_title=correct_slug)
        elif record.status != 'Approved':
            messages.error(request, 'Can not save Pending papers!')
            return redirect('view_paper', paper_id=record.id, paper_title=correct_slug)
        
        toggle_save_paper(request, record)
        return redirect('view_paper', paper_id=record.id, paper_title=correct_slug)

    _prepare_record_preview(record)
    file_name = Path(record.file.name).name if record.file else ''
    image_attachments = _get_multi_image_attachments(record)

    return render(request, 'home/view_paper.html', {
        'record': record,
        'file_name': file_name,
        'is_pdf': record.is_pdf,
        'is_image': record.is_image,
        'image_attachments': image_attachments,
        'has_multiple_attachments': len(image_attachments) > 1,
        'preview_url': reverse('paper_preview', args=[record.id, correct_slug]),
        'user_is_admin': is_admin,
        'is_owner': is_owner,
        'is_saved': record.saved_by.filter(id=request.user.id).exists() if request.user.is_authenticated else False,
        'report_reason_choices': REPORT_REASON_CHOICES,
    })

@ratelimit(key='user_or_ip', rate='100/m', block=True)
@xframe_options_sameorigin
def paper_preview(request, paper_id, paper_title=None):
    is_admin = request.user.is_superuser

    try:
        record = Record.objects.select_related('course__uni').get(id=paper_id)
    except Record.DoesNotExist:
        raise Http404('Past Paper does not exist!')

    is_owner = request.user.is_authenticated and record.uploaded_email == request.user.email

    if record.status != 'Approved' and not (is_admin or is_owner):
        raise Http404('Past Paper does not exist!')

    correct_slug = slugify(record.title)
    if paper_title is None or correct_slug != paper_title:
        raise Http404('Past Paper does not exist!')

    content_type, _ = mimetypes.guess_type(record.file.name)
    response = FileResponse(record.file.open('rb'), content_type=content_type or 'application/octet-stream')
    response['Content-Disposition'] = f'inline; filename="{Path(record.file.name).name}"'
    return response

@ratelimit(key='user_or_ip', rate='100/m', block=True)
def toggle_save_paper(request, record):
    is_saved = record.saved_by.filter(id=request.user.id).exists()
    if is_saved:
        record.saved_by.remove(request.user)
        messages.success(request, "Removed from your collection.")
    else:
        messages.success(request, "Saved to your collection!")
        record.saved_by.add(request.user)

def compress_image(uploaded_image):
    """Automatically shrinks huge smartphone photos to web-friendly sizes."""
    if uploaded_image.name.lower().endswith('.pdf'):
        return uploaded_image

    img = Image.open(uploaded_image)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Resize width to 1200px (keeps text crystal clear, drops megabytes)
    output_width = 1200
    w_percent = (output_width / float(img.size[0]))
    h_size = int((float(img.size[1]) * float(w_percent)))
    img = img.resize((output_width, h_size), Image.Resampling.LANCZOS)
    
    output_io = io.BytesIO()
    img.save(output_io, format='JPEG', quality=75) # 75% compression factor
    output_io.seek(0)
    
    return InMemoryUploadedFile(
        output_io, 'ImageField', uploaded_image.name, 
        'image/jpeg', output_io.getbuffer().nbytes, None
    )

@login_required(login_url='account_login')
@ratelimit(key='user_or_ip', rate='5/m', method='POST', block=True)
def upload(request):
    """Render the upload form or save a paper submission from the form POST."""
    if request.method == 'POST':
        if getattr(request, 'limited', False):
            messages.error(request, 'You can only upload 5 files every minute.')
            return redirect('upload')

        form = AcademicUploadForm(request.POST)

        if not form.is_valid():
            first_error = list(form.errors.values())[0][0]
            messages.error(request, f"Form validation failed: {first_error}")
            return redirect('upload')

        university = _normalize_university_input(form.cleaned_data.get('university'))
        year = _clean_text_input(form.cleaned_data.get('year'))
        title = _clean_text_input(form.cleaned_data.get('title'))
        semester = _clean_text_input(form.cleaned_data.get('semester'))
        session = _clean_text_input(form.cleaned_data.get('session'))
        program = _clean_text_input(form.cleaned_data.get('program'))
        course_name = _clean_text_input(form.cleaned_data.get('course_name'))
        term = _clean_text_input(form.cleaned_data.get('term'))
        
        # Pull initial list for base counts and validations
        paper_files = request.FILES.getlist('paper_file')
        
        uploaded_by = request.user.get_full_name().strip() or request.user.email or request.user.get_username()
        uploaded_email = request.user.email
        is_multi_image_upload = len(paper_files) > 1
        batch_id = uuid.uuid4().hex if is_multi_image_upload else None

        if not all([university, year, title, semester, session, program, course_name, term]) or not paper_files:
            messages.error(request, 'All fields are required!')
            return redirect('upload')

        if not year.isdigit() or len(year) != 4:
            messages.error(request, 'Year must be a 4-digit value.')
            return redirect('upload')

        if len(paper_files) > 3:
            messages.error(request, 'You can upload up to 3 image files at a time.')
            return redirect('upload')

        # Run your strict type validations
        if is_multi_image_upload:
            for f in paper_files:
                if not _is_image_uploaded_file(f):
                    messages.error(request, f"File '{f.name}' is not a valid image. Multi-file uploads only support images.")
                    return redirect('upload')

        for paper_file in paper_files:
            file_error = _validate_uploaded_file(paper_file)
            if file_error:
                messages.error(request, file_error)
                return redirect('upload')

        try:
            with transaction.atomic():
                # ==============================================================
                # THE CHIEF FIX: Pull a pristine copy from request.FILES right now!
                # ==============================================================
                pristine_files = request.FILES.getlist('paper_file')
                primary_file = pristine_files[0]

                # FAIL-SAFE RECONSTRUCTION: If Cloudinary's backend wrapper already converted 
                # this item to a string name, wrap its raw memory stream container back up manually.
                if isinstance(primary_file, str):
                    from django.core.files.base import ContentFile
                    raw_data = request.FILES['paper_file'].read()
                    primary_file = ContentFile(raw_data, name=primary_file)

                uni = _get_or_create_normalized_uni(university)
                if not uni:
                    messages.error(request, 'Please choose a valid university.')
                    return redirect('upload')

                course = _get_or_create_normalized_course(
                    uni=uni, semester=semester, program=program,
                    course_name=course_name, year=year, term=term, session=session,
                )

                record = Record(
                    course=course,
                    title=title,
                    uploaded_by=uploaded_by,
                    uploaded_email=uploaded_email,
                    status='Pending',
                )

                if is_multi_image_upload and batch_id:
                    primary_storage_name = _build_multi_image_storage_name(batch_id, 1, primary_file.name)
                else:
                    primary_storage_name = primary_file.name

                # ONLY attempt image compression if the file stream is genuinely an image asset
                if _is_image_uploaded_file(primary_file):
                    primary_file = compress_image(primary_file)

                # This line will now execute flawlessly because primary_file is a file object container
                record.file.save(primary_storage_name, primary_file, save=False)
                record.save()

                if is_multi_image_upload and batch_id:
                    for index, paper_file in enumerate(pristine_files[1:], start=2):
                        # Apply identical protection logic for all sub-attachments
                        if isinstance(paper_file, str):
                            from django.core.files.base import ContentFile
                            # Accessing multi-upload indexes dynamically
                            raw_data = request.FILES.getlist('paper_file')[index-1].read()
                            paper_file = ContentFile(raw_data, name=paper_file)

                        storage_name = _build_multi_image_storage_name(batch_id, index, paper_file.name)
                        
                        if _is_image_uploaded_file(paper_file):
                            paper_file = compress_image(paper_file)

                        attachment = PaperAttachment(record=record)
                        attachment.file.save(storage_name, paper_file, save=False)
                        attachment.save()

            messages.success(request, 'Successfully Uploaded! It will appear once approved by an admin.')
            return redirect('home')
            
        except Exception as exc:
            print(f"--- UPLOAD CRASH DETAILS: {exc} ---") 
            import traceback
            traceback.print_exc()
            messages.error(request, f"Upload handling failed!")
            return redirect('upload')

    return render(request, 'home/upload.html', {
        'available_courses': AVAILABLE_COURSES,
        'available_programs': AVAILABLE_PROGRAMS,
        'available_universities': list(AVAILABLE_UNIVERSITIES.values()),
    })

@ratelimit(key='user_or_ip', rate='50/m', block=True)
@login_required(login_url='account_login')
def profile(request):
    """Render the user's profile page."""

    form = ProfileDashboardFilterForm(request.GET)
    if not form.is_valid():
        messages.error(request, "Invalid filter configuration!")
        return redirect('profile')

    status = _clean_text_input(form.cleaned_data.get('status'))
    view_mode = _clean_text_input(form.cleaned_data.get('view') or 'uploaded').lower() #also change
    page_number = form.cleaned_data.get('page_number') or 1 

    if view_mode not in ['saved', 'uploaded']:
        return HttpResponseBadRequest("Invalid View")

    show_saved_files = False
    if view_mode == 'saved':
        show_saved_files = True
    is_admin = request.user.is_superuser

    if show_saved_files:
        records = Record.objects.select_related('course__uni').filter(saved_by=request.user, status='Approved').order_by('-id')
    else:
        records = Record.objects.select_related('course__uni').filter(uploaded_email=request.user.email).order_by('-id')

    approved_count = pending_count = saved_count = 0
    if status:
        records = records.filter(status__iexact=status)

    if view_mode == 'uploaded':
        approved_count = Record.objects.filter(uploaded_email=request.user.email, status='Approved').count()
        pending_count = Record.objects.filter(uploaded_email=request.user.email, status='Pending').count()
    elif view_mode == 'saved':
        saved_count = records.count()

    paginator = Paginator(records, 10)

    if not form.is_valid():
        # If they inject an integer larger than 9999 or trash text, the form catches it
        page_number = 1
    else:
        page_number = form.cleaned_data.get('page') or 1
    records = paginator.get_page(page_number)

    _attach_preview_metadata(records)

    pagination = _build_compact_page_window(paginator, records.number)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    page_query_string = query_params.urlencode()

    toggle_view_params = request.GET.copy()
    toggle_view_params.pop('page', None)
    toggle_view_params['view'] = 'uploaded' if show_saved_files else 'saved'
    view_toggle_query_string = toggle_view_params.urlencode()

    return render(request, 'home/profile.html', {
        'records': records,
        'approved_count': approved_count,
        'paginator': paginator,
        'page_number': page_number,
        'pending_count': pending_count,
        'is_admin': is_admin,
        'saved_count': saved_count,
        'show_saved_files': show_saved_files,
        'view_toggle_label': 'Uploaded Files' if show_saved_files else 'Saved Files',
        'section_title': 'Your Saved Files' if show_saved_files else 'Your Uploaded Files',
        'view_toggle_query_string': view_toggle_query_string,
        'selected_status': status,
        'page_query_string': page_query_string,
        **pagination,
        'available_statuses': [
            {'label': 'Pending', 'value': 'Pending'},
            {'label': 'Approved', 'value': 'Approved'},
        ],
    })

@ratelimit(key='user_or_ip', rate='3/m', method='POST', block=True)
@login_required(login_url='account_login')
def delete_record(request, paper_id):
    """Delete one of the current user's uploaded papers."""

    if getattr(request, 'limited', False):
            messages.error(request, 'You can only delete 3 papers every minute.')
            return redirect('profile')

    record = get_object_or_404(Record.objects.select_related('course__uni'), pk=paper_id)
    if record.uploaded_email == request.user.email:
        is_owner = True
    else:
        is_owner = False
    is_admin = request.user.is_superuser

    if not (is_owner or is_admin):
        messages.error(request, 'Can not delete paper!')
        return redirect('profile')

    if request.method != 'POST':
        return redirect('profile')

    if record.file:
        _delete_multi_image_attachments(record)
        record.file.delete(save=False)
    record.delete()

    messages.success(request, 'Paper deleted successfully.')

    next_url = request.META.get('HTTP_REFERER')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect('profile')

@ratelimit(key='user_or_ip', rate='3/m', method='POST', block=True)
@login_required(login_url='account_login')
def delete_user(request, user_id):
    User = get_user_model()
    user = get_object_or_404(User, pk=user_id)
    is_owner = user.pk == request.user.pk
    is_admin = request.user.is_superuser

    if request.method != 'POST':
        return redirect('profile')

    if not is_owner:
        messages.error(request, 'Can not delete account!')
        return redirect('profile')
    if is_admin:
        messages.error(request, 'Can not delete admin\'s account.')
        return redirect('profile')

    if user and is_owner:
        logout(request)
        user.delete()
        messages.success(request, 'Account deleted successfully.')
        return redirect('home')

    messages.error(request, 'Can not delete account!')
    return redirect('profile')

@ratelimit(key='user_or_ip', rate='50/m', block=True)
def about(request):
    """Render the about page."""
    return render(request, 'home/about.html')

@ratelimit(key='user_or_ip', rate='3/m', method='POST', block=True)
def login_page(request):
    if getattr(request, 'limited', False):
            messages.error(request, 'You can only login 3 times every minute.')
            return redirect('account_login')

    form = LoginRedirectForm(request.POST or request.GET)
    if form.is_valid():
        next_url = form.cleaned_data.get('next') or 'home'################ Gemini latest prompt DO THE CHANGESS!
    else:
        next_url = 'home'
    # Explicitly check if the target URL safely belongs to your website
    if not url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}):
        next_url = 'home' 

    if request.user.is_authenticated:
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect('home')

    return render(request, 'home/login.html', {'next': next_url})

@login_required(login_url='account_login')
def logout_page(request):
    if request.method == "POST":
        logout(request)
        messages.success(request, "You have been logged out successfully.")
        return redirect("home")
    return redirect("account_login")

@ratelimit(key='user_or_ip', rate='50/m', block=True)
def privacy_policy(request):
    return render(request, 'home/privacy_policy.html')

@ratelimit(key='user_or_ip', rate='50/m', block=True)
def terms_and_conditions(request):
    return render(request, 'home/terms_and_conditions.html')