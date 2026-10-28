from rest_framework.permissions import DjangoModelPermissions


class ArchivableModelPermissions(DjangoModelPermissions):
    """Model permissions where archive and restore count as changes, not additions."""

    def has_permission(self, request, view):
        if getattr(view, "action", None) in {"archive", "restore"}:
            if not request.user or not request.user.is_authenticated:
                return False
            perms = self.get_required_permissions("PATCH", self._queryset(view).model)
            return request.user.has_perms(perms)
        return super().has_permission(request, view)
