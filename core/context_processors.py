from django.conf import settings


def version(request):
    """Expose VERSION to all templates."""
    return {"VERSION": settings.VERSION}
