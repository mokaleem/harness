"""Read-only published projections, including request-scoped skill discovery."""

from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage
from deerflow.skills.types import SkillCategory


class RegistryWrites:
    def write_custom_skill(self, *args, **kwargs):
        raise PermissionError("Use the enterprise draft/review/publication workflow")

    def remove_custom_skill_file(self, *args, **kwargs):
        raise PermissionError("Published skills are immutable")

    def delete_custom_skill(self, *args, **kwargs):
        raise PermissionError("Published skills are immutable")

    async def ainstall_skill_from_archive(self, *args, **kwargs):
        raise PermissionError("Register and review the external skill package before deployment")

    def get_skill_enabled_state(self, name, category):
        return True  # Selection is an activation projection, not an API-writable toggle.

    def set_skill_enabled_state(self, *args, **kwargs):
        raise PermissionError("Capability activation is owned by the authoritative registry")

    def _iter_skill_files(self):
        for entry in LocalSkillStorage._iter_skill_files(self):
            if entry[0] == SkillCategory.PUBLIC:
                yield entry

    def load_skills(self, *, enabled_only=False):
        import dataclasses

        # Activation owns enabled state; API-writable switches are not another writer.
        return [dataclasses.replace(skill, enabled=True) for skill in LocalSkillStorage.load_skills(self, enabled_only=False)]


class RegistrySkillStorage(RegistryWrites, LocalSkillStorage):
    pass


class RegistryUserSkillStorage(RegistryWrites, UserScopedSkillStorage):
    pass
