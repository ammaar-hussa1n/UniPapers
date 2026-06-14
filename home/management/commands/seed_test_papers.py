from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from home.models import Course, Record, Uni


class Command(BaseCommand):
    help = 'Seed repeated paper records from one PDF for local pagination testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--pdf',
            type=Path,
            required=True,
            help='Path to the source PDF file to reuse for every record.',
        )
        parser.add_argument(
            '--count',
            type=int,
            default=100,
            help='Number of records to create. Default: 100.',
        )
        parser.add_argument(
            '--title-prefix',
            default='PF Test Paper',
            help='Prefix used to generate unique titles.',
        )
        parser.add_argument(
            '--university',
            default='Sir Syed University',
            help='University stored on every record.',
        )
        parser.add_argument(
            '--program',
            default='Computer Science',
            help='Program stored on every record.',
        )
        parser.add_argument(
            '--semester',
            default='1st Semester',
            help='Semester stored on every record.',
        )
        parser.add_argument(
            '--course-name',
            default='Programming Fundamentals (PF)',
            help='Course name stored on every record.',
        )
        parser.add_argument(
            '--year',
            default='2026',
            help='Year stored on every record.',
        )
        parser.add_argument(
            '--session',
            default='Fall',
            help='Session stored on every record.',
        )
        parser.add_argument(
            '--term',
            default='Mid Term',
            help='Term stored on every record.',
        )
        parser.add_argument(
            '--uploaded-by',
            default='Pagination Test Seeder',
            help='Uploaded-by value stored on every record.',
        )
        parser.add_argument(
            '--uploaded-email',
            default='seeder@example.com',
            help='Uploaded-email value stored on every record.',
        )
        parser.add_argument(
            '--status',
            default='Pending',
            choices=[choice for choice, _ in Record.STATUS_CHOICES],
            help='Status stored on every record.',
        )
        parser.add_argument(
            '--start-index',
            type=int,
            default=1,
            help='Starting number for generated titles. Default: 1.',
        )

    def handle(self, *args, **options):
        pdf_path = options['pdf'].expanduser()
        count = options['count']
        start_index = options['start_index']

        if not pdf_path.is_file():
            raise CommandError(f'PDF file does not exist: {pdf_path}')
        if pdf_path.suffix.lower() != '.pdf':
            raise CommandError('The source file must be a PDF.')
        if count <= 0:
            raise CommandError('--count must be greater than zero.')
        if start_index <= 0:
            raise CommandError('--start-index must be greater than zero.')

        pdf_bytes = pdf_path.read_bytes()
        created_records = []

        for index in range(start_index, start_index + count):
            title = f"{options['title_prefix']} {index:03d}"
            uni, _ = Uni.objects.get_or_create(uni_name=options['university'])
            course, _ = Course.objects.get_or_create(
                uni=uni,
                semester=options['semester'],
                program=options['program'],
                course_name=options['course_name'],
                year=str(options['year']),
                term=options['term'],
                session=options['session'],
            )
            record = Record(
                course=course,
                title=title,
                uploaded_by=options['uploaded_by'],
                uploaded_email=options['uploaded_email'],
                status=options['status'],
            )
            file_name = f'{pdf_path.stem}_{index:03d}.pdf'
            record.file.save(file_name, ContentFile(pdf_bytes), save=False)
            record.save()
            created_records.append(record.pk)

        self.stdout.write(
            self.style.SUCCESS(
                f'Created {len(created_records)} records for {options["university"]} / '
                f'{options["program"]} / {options["semester"]} / {options["course_name"]}.'
            )
        )