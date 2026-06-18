import os
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils.text import slugify
from cloudinary_storage.storage import RawMediaCloudinaryStorage

def get_upload_path(instance, filename):
    """
    Generates an isolated, immutable storage path.
    Combines timestamping with a hex UUID token to guarantee unique file structures 
    while preserving the original academic extension layout on disk.
    """
    ext = filename.split('.')[-1]
    clean_name = slugify('.'.join(filename.split('.')[:-1]))
    unique_filename = f"{clean_name}_{uuid.uuid4().hex[:8]}.{ext}"
    
    # Files remain here safely forever; state checks rely strictly on DB status fields
    return os.path.join('vault/papers/', unique_filename)

class Uni(models.Model):
    uni_name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.uni_name

class Course(models.Model):
    uni = models.ForeignKey(Uni, on_delete=models.CASCADE)
    semester = models.CharField(max_length=20)
    program = models.CharField(max_length=255)
    course_name = models.CharField(max_length=255)
    year = models.IntegerField(validators=[MinValueValidator(2000), MaxValueValidator(2030)])
    term = models.CharField(max_length=20)
    session = models.CharField(max_length=20)

    class Meta:
        # Ensures no identical duplicates can occupy your academic courses lookup dictionary
        unique_together = ('uni', 'semester', 'program', 'course_name', 'year', 'term', 'session')
    
    def __str__(self):
        return f"{self.course_name} ({self.term} - {self.year})"

class Record(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
    ]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to=get_upload_path, storage=RawMediaCloudinaryStorage(), max_length=500)

    file_extension = models.CharField(max_length=10, blank=True, default='')

    uploaded_by = models.CharField(max_length=255, default='Anonymous')
    uploaded_email = models.EmailField(max_length=255, default='anonymous@example.com', db_index=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending', db_index=True)
    msg = models.TextField(blank=True, null=True)
    
    saved_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='saved_papers', blank=True)

    def __str__(self):
        return self.title

    @property
    def uni_name(self):
        return self.course.uni.uni_name if self.course and self.course.uni_id else ''

    @property
    def program(self):
        return self.course.program if self.course_id else ''

    @property
    def semester(self):
        return self.course.semester if self.course_id else ''

    @property
    def course_name(self):
        return self.course.course_name if self.course_id else ''

    @property
    def year(self):
        return self.course.year if self.course_id else ''

    @property
    def term(self):
        return self.course.term if self.course_id else ''

    @property
    def session(self):
        return self.course.session if self.course_id else ''

    @property
    def title_slug(self):
        """URL-safe slug for this paper's title.

        Falls back to 'paper' when the title has no slug-able characters
        (e.g. titles written entirely in non-Latin scripts, or only symbols).
        The URL patterns use a <str> converter that rejects empty segments,
        so an empty slug would otherwise raise NoReverseMatch (HTTP 500).
        """
        return slugify(self.title) or 'paper'

    def save(self, *args, **kwargs):
        if self.file and not self.file_extension:
            # self.file.name at this point is the original uploaded string (e.g., 'test.pdf')
            ext = os.path.splitext(self.file.name)[1].lower()
            self.file_extension = ext
        super().save(*args, **kwargs)
        
    class Meta:
        ordering = ['-uploaded_at']

# ADD THIS NEW MODEL TO TRACK EXTRA PAGES/IMAGES
class PaperAttachment(models.Model):
    # Links many secondary pages back to the one primary Record row
    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name='attachments')
    
    # Bump the max_length here too to protect multi-file uploads!
    file = models.FileField(upload_to=get_upload_path, max_length=500) 
    uploaded_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Attachment for {self.record.title} ({self.id})"

class Report(models.Model):
    record = models.ForeignKey('Record', on_delete=models.CASCADE, related_name='reports')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Report on {self.record.title}"

class ReportedRecord(Record):
    class Meta:
        proxy = True
        verbose_name = 'Reported Paper'
        verbose_name_plural = 'Reported Papers'