"""Lazy compatibility exports; new callers import explicit composition modules."""
from importlib import import_module

_EXPORTS = {'RealtimeEntryPipeline': 'pipelines.legacy.realtime_entry_pipeline', 'CustomerSession': 'pipelines.legacy.realtime_entry_pipeline', 'NewCustomerCallback': 'pipelines.legacy.realtime_entry_pipeline', 'MemberEntryCallback': 'pipelines.legacy.realtime_entry_pipeline', 'AnonymousVisitCallback': 'pipelines.legacy.realtime_entry_pipeline', 'create_realtime_pipeline': 'pipelines.legacy.realtime_entry_pipeline', 'HistoricalVideoAnalysisPipeline': 'pipelines.legacy.historical_video_pipeline', 'create_historical_pipeline': 'pipelines.legacy.historical_video_pipeline', 'CustomerProfilePipeline': 'pipelines.legacy.customer_profile_pipeline', 'create_customer_profile_pipeline': 'pipelines.legacy.customer_profile_pipeline', 'DeviceIdentityRuntime': 'pipelines.device_identity_runtime', 'create_device_identity_runtime': 'pipelines.device_identity_runtime', 'MemberBusinessRuntime': 'pipelines.legacy.member_business_runtime', 'create_member_business_runtime': 'pipelines.legacy.member_business_runtime', 'StoreRuntime': 'pipelines.store_runtime', 'StoreRuntimeDependencies': 'pipelines.store_runtime'}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value
