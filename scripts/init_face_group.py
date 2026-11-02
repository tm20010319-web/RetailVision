"""Initialize or verify the device face group for member registration.

Run this script once during deployment to create a face group on the
camera device and persist its UUID to the environment configuration.

Usage::

    python scripts/init_face_group.py
    python scripts/init_face_group.py --group-name my_store_members
    python scripts/init_face_group.py --verify

The script prints the resolved group UUID and writes a ``.env`` snippet
that can be appended to the deployment environment file.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid

from agent.platform.camera_face_api import CameraFaceApiClient
from agent.config import settings

logger = logging.getLogger(__name__)


def build_client() -> CameraFaceApiClient:
    """Build a camera API client from current settings."""
    missing = []
    for attr in (
        "CAMERA_FACE_DEVICE_ID",
        "CAMERA_FACE_BASE_URL",
        "CAMERA_FACE_USERNAME",
        "CAMERA_FACE_PASSWORD",
        "CAMERA_FACE_UNIQUE_CODE",
    ):
        if not getattr(settings, attr, ""):
            missing.append(attr)
    if missing:
        logger.error(
            "Missing required settings: %s. "
            "Set them via environment variables before running this script.",
            ", ".join(missing),
        )
        sys.exit(1)
    return CameraFaceApiClient.from_settings(settings)


def verify_existing_group(client: CameraFaceApiClient, group_uuid: str) -> None:
    """Verify that a face group with the given UUID exists on the device."""
    groups = client.query_face_groups()
    match = next(
        (g for g in groups if g["group_uuid"] == group_uuid),
        None,
    )
    if match is None:
        logger.error(
            "Group UUID %s not found on device. Available groups: %s",
            group_uuid,
            [g["group_name"] for g in groups],
        )
        sys.exit(1)
    logger.info("Verified face group: %s (UUID=%s, persons=%d)",
                match["group_name"], match["group_uuid"], match["person_count"])
    print(f"\nGroup verified: {match['group_name']}")
    print(f"  UUID: {match['group_uuid']}")
    print(f"  GroupID: {match['group_id']}")
    print(f"  Persons: {match['person_count']}")
    print_env_snippet(match["group_uuid"], match["group_id"])


def create_new_group(client: CameraFaceApiClient, group_name: str) -> None:
    """Create a new face group on the device."""
    group_uuid = uuid.uuid4().hex
    logger.info("Creating face group '%s' with UUID %s", group_name, group_uuid)
    returned_uuid = client.create_face_group(
        group_name=group_name,
        group_uuid=group_uuid,
    )
    logger.info("Face group created. Returned UUID: %s", returned_uuid)

    groups = client.query_face_groups()
    match = next(
        (g for g in groups if g["group_uuid"] == returned_uuid),
        None,
    )
    group_id = match["group_id"] if match else -1

    print(f"\nFace group created: {group_name}")
    print(f"  UUID: {returned_uuid}")
    print(f"  GroupID: {group_id}")
    print_env_snippet(returned_uuid, group_id)


def print_env_snippet(group_uuid: str, group_id: int) -> None:
    """Print environment variable snippet for the user to save."""
    print("\n--- Add to your environment / .env ---")
    print(f"CAMERA_FACE_GROUP_UUID={group_uuid}")
    print(f"CAMERA_FACE_GROUP_ID={group_id}")
    print("-------------------------------------\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Initialize device face group.")
    parser.add_argument(
        "--group-name",
        default=settings.CAMERA_FACE_GROUP_NAME,
        help="Name for the new face group (default: %(default)s)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Only verify the existing CAMERA_FACE_GROUP_UUID",
    )
    args = parser.parse_args()

    client = build_client()

    if args.verify or settings.CAMERA_FACE_GROUP_UUID:
        group_uuid = settings.CAMERA_FACE_GROUP_UUID
        if not group_uuid:
            logger.error("CAMERA_FACE_GROUP_UUID is not set. Run without --verify to create one.")
            sys.exit(1)
        verify_existing_group(client, group_uuid)
    else:
        create_new_group(client, args.group_name)


if __name__ == "__main__":
    main()
