from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"
    VIEWER = "viewer"


class SharePermission(str, Enum):
    READ = "read"
    DOWNLOAD = "download"
