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


class ViewModelPermissions(DjangoModelPermissions):
    """Model permissions where reading needs the model's view permission too.

    For records such as the audit log, which staff without that permission shouldn't read.
    """

    perms_map = {
        **DjangoModelPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
    }

    def has_permission(self, request, view):
        # The export is a POST because it writes an `audit.exported` event, but it's a read.
        if getattr(view, "action", None) == "export":
            if not request.user or not request.user.is_authenticated:
                return False
            return request.user.has_perms(self.get_required_permissions("GET", view.model))
        return super().has_permission(request, view)

    def _queryset(self, view):
        # Only the model matters here. The view's own queryset looks up the customer in the URL,
        # which would tell a user without the permission which customers exist.
        return view.model.all_customers.none()


class JobPermissions(ViewModelPermissions):
    """Reading jobs needs their view permission; cancelling counts as a change."""

    def has_permission(self, request, view):
        if getattr(view, "action", None) == "cancel":
            if not request.user or not request.user.is_authenticated:
                return False
            return request.user.has_perms(self.get_required_permissions("PATCH", view.model))
        return super().has_permission(request, view)
