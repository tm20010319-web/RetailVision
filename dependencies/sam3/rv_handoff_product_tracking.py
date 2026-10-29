"""Use the module layout of the active RetailVision checkout."""
import importlib
import importlib.util
_current_layout = importlib.util.find_spec("agent.behavior") is not None
_module = importlib.import_module('agent.behavior.sam31_product_tracking' if _current_layout else 'agent.sam31_product_tracking')
globals().update({key: value for key, value in vars(_module).items() if not key.startswith("_")})
