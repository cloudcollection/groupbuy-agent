"""Local, key-free preflight and controlled operator guidance."""
import importlib.util
import os
import sys
from pathlib import Path
from . import __version__

UI_MODULES = ('pywinauto', 'rapidocr_onnxruntime', 'psutil', 'PIL', 'win32gui')
NEXT_ACTIONS = {
    'optional_ui_dependencies_missing': 'install_ui_dependencies',
    'temp_directory_required': 'configure_d_drive_temp',
    'ui_calibration_required': 'calibrate_page_and_save_new_workspace',
    'ui_not_enabled': 'allow_local_ui',
    'manual_retry_required': 'fix_cause_then_retry',
    'capture_no_reports': 'check_reqable_rule_url_and_new_traffic',
    'report_not_authorized': 'copy_complete_local_upload_url',
    'unsupported_report_encoding': 'select_gzip_or_no_compression',
    'invalid_report': 'check_reqable_report_format',
    'capture_incomplete_or_not_reporting': 'check_report_counters_and_pagination',
    'verification_or_login': 'handle_login_or_verification_manually',
    'foreground_changed': 'restore_miniprogram_then_retry',
    'observed_control_missing': 'correct_observed_control_label',
    'ambiguous_observed_control': 'choose_unique_observed_control',
    'target_or_filter_not_visible': 'verify_city_center_and_filters',
    'scroll_budget': 'verify_same_list_before_resume',
    'local_export_required': 'provide_local_export',
    'local_input_required': 'provide_local_input',
    'agent_no_progress': 'review_current_stage_and_tool_support',
}


def next_action(code):
    return NEXT_ACTIONS.get(code, 'review_local_report' if code else None)


def doctor(config):
    """No network, API keys, UI capture, state recovery or progress writes."""
    missing = []
    for name in UI_MODULES:
        try:
            available = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            missing.append(name)
    live = config['mode'] == 'live'
    codes = []
    if live and (os.name != 'nt' or missing):
        codes.append('optional_ui_dependencies_missing')
    if live and any(t.get('ui_profile', {}).get('calibration_required') for t in config['tasks']):
        codes.append('ui_calibration_required')
    temp = os.environ.get('GB_TEMP_DIR', '')
    temp_ready = bool(temp) and (os.name != 'nt' or Path(temp).resolve().drive.upper() == 'D:')
    if live and not temp_ready:
        codes.append('temp_directory_required')
    return {'schema': 'groupbuy.doctor.v1', 'version': __version__,
            'status': 'ATTENTION_REQUIRED' if codes else 'READY',
            'platforms': ['dianping'], 'core_ready': sys.version_info >= (3, 10),
            'python_version': '.'.join(map(str, sys.version_info[:3])),
            'live_environment': os.name == 'nt' and not missing,
            'missing_ui_dependencies': missing, 'temp_ready': temp_ready,
            'capture_enabled': config.get('_capture', {}).get('enabled', False),
            'schedule_enabled': config['schedule']['enabled'], 'live_verified': False,
            'checks': [{'code': code, 'next_action': next_action(code)} for code in codes]}
