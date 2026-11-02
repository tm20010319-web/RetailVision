"""Strict loader for DPI-01C product camera configurations.

The loader keeps formal catalog product types separate from the legacy Stage7C
tracking type.  It is intentionally scoped to the dense-product validation
configuration and does not alter the legacy ``cam02`` loader or meanings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from agent.behavior.sam31_product_tracking import ProductDisplaySurface


SUPPORTED_TRACKING_PRODUCT_TYPES = frozenset({"smart_toy", "smart_guitar"})


class ProductCameraConfigError(ValueError):
    """Fail-closed configuration error with a stable message."""


SUPPORTED_DISPLAY_EVIDENCE_MODES = frozenset({"mask_surface_overlap", "fixed_support_contact"})


@dataclass(frozen=True)
class ConfiguredProduct:
    product_key: str
    product_name: str
    product_type: str
    tracking_product_type: str


@dataclass(frozen=True)
class ProductCameraConfig:
    path: Path
    config_version: str
    camera_id: str
    frame_size: tuple[int, int]
    selected_product_keys: tuple[str, ...]
    products: Mapping[str, ConfiguredProduct]
    product_display_surface: ProductDisplaySurface
    surface_config: Mapping[str, object]
    support_contact_points: Mapping[str, tuple[float, float]]
    display_evidence_mode: str
    support_contact_tolerance: float
    calibration_status: str
    product_track_lost_after_windows: int
    raw: Mapping[str, object]

    @property
    def tracking_products(self) -> dict[str, str]:
        return {
            key: self.products[key].tracking_product_type
            for key in self.selected_product_keys
        }


def load_dpi01c_product_camera_config(path: str | Path) -> ProductCameraConfig:
    """Load a dense-product config and require explicit tracking mappings."""

    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProductCameraConfigError(f"cannot load product camera config: {config_path}") from error
    if not isinstance(payload, dict):
        raise ProductCameraConfigError("product camera config must be an object")

    config_version = _required_string(payload, "config_version")
    camera_id = _required_string(payload, "camera_id")
    if camera_id == "192.0.2.3" or "." in camera_id:
        raise ProductCameraConfigError("camera_id must be a logical identifier")
    frame_size_value = payload.get("frame_size")
    if frame_size_value != [1280, 720]:
        raise ProductCameraConfigError("product camera frame_size must be [1280, 720]")
    selected_value = payload.get("selected_product_keys")
    if not isinstance(selected_value, list) or not selected_value:
        raise ProductCameraConfigError("selected_product_keys is required")
    selected = tuple(str(value) for value in selected_value)
    if len(set(selected)) != len(selected):
        raise ProductCameraConfigError("selected_product_keys must be unique")
    products_value = payload.get("products")
    if not isinstance(products_value, dict):
        raise ProductCameraConfigError("products is required")
    products: dict[str, ConfiguredProduct] = {}
    for key in selected:
        entry = products_value.get(key)
        if not isinstance(entry, dict):
            raise ProductCameraConfigError(f"missing product configuration: {key}")
        product_name = _required_string(entry, "product_name", key)
        product_type = _required_string(entry, "product_type", key)
        tracking_type = _required_string(entry, "tracking_product_type", key)
        if tracking_type not in SUPPORTED_TRACKING_PRODUCT_TYPES:
            raise ProductCameraConfigError(
                f"unsupported tracking_product_type for {key}: {tracking_type}"
            )
        products[key] = ConfiguredProduct(
            product_key=key,
            product_name=product_name,
            product_type=product_type,
            tracking_product_type=tracking_type,
        )

    surface = payload.get("product_display_surface")
    if not isinstance(surface, dict):
        raise ProductCameraConfigError("product_display_surface is required")
    points = surface.get("points")
    if not isinstance(points, list) or len(points) < 3:
        raise ProductCameraConfigError("product_display_surface.points is required")
    try:
        normalized_points = tuple((float(point[0]), float(point[1])) for point in points)
        display_surface = ProductDisplaySurface(normalized_points)
    except (TypeError, ValueError, IndexError) as error:
        raise ProductCameraConfigError("invalid product_display_surface.points") from error

    display_mode = str(surface.get("display_evidence_mode", "mask_surface_overlap"))
    if display_mode not in SUPPORTED_DISPLAY_EVIDENCE_MODES:
        raise ProductCameraConfigError(f"unsupported display_evidence_mode: {display_mode}")
    tolerance = float(surface.get("support_contact_tolerance", 0.08))
    if not 0 <= tolerance <= 1:
        raise ProductCameraConfigError("support_contact_tolerance must be between 0 and 1")
    calibration_status = str(surface.get("calibration_status", payload.get("calibration_status", "PENDING_OPERATOR_CONFIRMATION")))
    if calibration_status not in {"CONFIRMED", "PENDING_OPERATOR_CONFIRMATION"}:
        raise ProductCameraConfigError("invalid calibration_status")
    lost_after_windows_value = payload.get("product_track_lost_after_windows", 3)
    try:
        product_track_lost_after_windows = int(lost_after_windows_value)
    except (TypeError, ValueError) as error:
        raise ProductCameraConfigError("product_track_lost_after_windows must be a positive integer") from error
    if product_track_lost_after_windows < 1:
        raise ProductCameraConfigError("product_track_lost_after_windows must be a positive integer")
    support_points_value = surface.get("support_contact_points", {})
    if not isinstance(support_points_value, dict):
        raise ProductCameraConfigError("product_display_surface.support_contact_points must be an object")
    support_points: dict[str, tuple[float, float]] = {}
    for key, point in support_points_value.items():
        if key not in selected:
            raise ProductCameraConfigError(f"support contact point has unknown product_key: {key}")
        if not isinstance(point, list) or len(point) != 2:
            raise ProductCameraConfigError(f"invalid support contact point for {key}")
        try:
            pixel_x, pixel_y = float(point[0]), float(point[1])
        except (TypeError, ValueError) as error:
            raise ProductCameraConfigError(f"invalid support contact point for {key}") from error
        if not (0 <= pixel_x <= 1280 and 0 <= pixel_y <= 720):
            raise ProductCameraConfigError(f"support contact point out of frame for {key}")
        support_points[str(key)] = (pixel_x / 1280.0, pixel_y / 720.0)

    return ProductCameraConfig(
        path=config_path,
        config_version=config_version,
        camera_id=camera_id,
        frame_size=(1280, 720),
        selected_product_keys=selected,
        products=products,
        product_display_surface=display_surface,
        surface_config=surface,
        support_contact_points=support_points,
        display_evidence_mode=display_mode,
        support_contact_tolerance=tolerance,
        calibration_status=calibration_status,
        product_track_lost_after_windows=product_track_lost_after_windows,
        raw=payload,
    )




def _required_string(mapping: Mapping[str, object], key: str, context: str | None = None) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        prefix = f" for {context}" if context else ""
        raise ProductCameraConfigError(f"{key}{prefix} is required")
    return value.strip()


def validate_formal_replay_readiness(
    config: ProductCameraConfig,
    mask_confirmations: Mapping[str, object],
) -> None:
    """Fail closed before a formal behavior replay can be started."""
    if config.calibration_status != "CONFIRMED":
        raise ProductCameraConfigError("calibration_status must be CONFIRMED")
    if config.display_evidence_mode != "fixed_support_contact":
        raise ProductCameraConfigError("formal replay requires fixed_support_contact")
    missing = set(config.selected_product_keys) - set(config.support_contact_points)
    if missing:
        raise ProductCameraConfigError(f"missing approved support contact points: {sorted(missing)}")
    if not 0 <= config.support_contact_tolerance <= 1:
        raise ProductCameraConfigError("invalid support contact tolerance")
    for key in config.selected_product_keys:
        confirmation = mask_confirmations.get(key)
        status = (
            confirmation.get("confirmation_status", confirmation.get("status"))
            if isinstance(confirmation, Mapping)
            else confirmation
        )
        if status != "confirmed":
            raise ProductCameraConfigError(f"mask confirmation required: {key}")
