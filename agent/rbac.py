"""Role-based access control — enforced at the agent gateway."""

from enum import StrEnum

# Tools that guests are allowed to use
GUEST_TOOLS = {"web_search", "web_extract", "read_file", "memory"}
# Tools that members cannot use (secret-adjacent)
MEMBER_BLOCKED_TOOLS: set[str] = set()  # none yet, reserved for future


class Role(StrEnum):
    OWNER = "owner"
    MEMBER = "member"
    GUEST = "guest"


class RBAC:
    def can_use_shell(self, role: Role) -> bool:
        return role in (Role.OWNER, Role.MEMBER)

    def can_use_tool(self, role: Role, tool_name: str) -> bool:
        if role == Role.OWNER:
            return True
        if role == Role.MEMBER:
            return tool_name not in MEMBER_BLOCKED_TOOLS
        # Guest
        return tool_name in GUEST_TOOLS

    def max_tokens(self, role: Role) -> int:
        if role == Role.OWNER:
            return 200_000
        if role == Role.MEMBER:
            return 100_000
        return 16_000  # guest

    def should_filter_output(self, role: Role) -> bool:
        return role == Role.GUEST

    def allowed_tools(self, role: Role) -> set[str] | None:
        """Return set of allowed tools, or None for unrestricted."""
        if role == Role.GUEST:
            return GUEST_TOOLS
        if role == Role.MEMBER:
            return None  # all except blocked
        return None  # owner: all
