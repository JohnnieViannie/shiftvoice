from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import AudioAsset


class Command(BaseCommand):
    help = "Delete expired audio assets (and their files)."

    def handle(self, *args, **options):
        now = timezone.now()
        qs = AudioAsset.objects.filter(expires_at__lt=now)
        total = qs.count()

        deleted = 0
        for asset in qs.iterator():
            # delete file from storage, then db row
            if asset.audio_file:
                asset.audio_file.delete(save=False)
            asset.delete()
            deleted += 1

        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted}/{total} expired audio assets."))

