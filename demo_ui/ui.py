
import streamlit as st
import json
import os
import sys
import datetime
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests
from local_ddna_helper import (
    get_local_ddna_topics,
    get_local_ddna_topic,
    get_latest_local_ddna,
    get_local_ddna_stats,
    search_local_ddna
)

# Setup paths
DEMO_UI_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(DEMO_UI_DIR)
BACKEND_DIR = os.path.join(PROJECT_ROOT, 'backend')
FLOW_DIR = os.path.join(BACKEND_DIR, 'flow')
RESTORATION_DIR = os.path.join(PROJECT_ROOT, 'Restoration_engine')

API_BASE_URL = os.environ.get("INTELLIOS_API_BASE_URL", "http://127.0.0.1:8000").rstrip('/')
try:
    RESTORE_REQUEST_TIMEOUT = float(os.environ.get("INTELLIOS_RESTORE_TIMEOUT", "45"))
except ValueError:
    RESTORE_REQUEST_TIMEOUT = 45.0

MAX_TABS_PER_BROWSER = 12
BROWSER_NAME_ALIASES = {
    "edge": "msedge",
    "microsoftedge": "msedge",
    "microsoft edge": "msedge",
    "google chrome": "chrome",
    "firefox": "firefox",
    "mozilla firefox": "firefox",
    "brave browser": "brave",
}
BROWSER_EXEC_PATHS = {
    "chrome": [
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ],
    "msedge": [
        r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    ],
    "firefox": [
        r"C:\\Program Files\\Mozilla Firefox\\firefox.exe",
        r"C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
    ],
    "brave": [
        r"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe",
        r"C:\\Program Files (x86)\\BraveSoftware\\Brave-Browser\\Application\\brave.exe",
    ],
    "opera": [
        r"C:\\Users\\%USERNAME%\\AppData\\Local\\Programs\\Opera\\opera.exe",
    ],
}
BROWSER_KEYS = {"chrome", "msedge", "firefox", "brave", "opera"}


def _expand_env_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return os.path.expanduser(os.path.expandvars(path))


def _default_browser_profile(browser: str) -> str:
    home_dir = os.path.expanduser("~")
    local_app = os.environ.get("LOCALAPPDATA") or os.path.join(home_dir, "AppData", "Local")
    roaming_app = os.environ.get("APPDATA") or os.path.join(home_dir, "AppData", "Roaming")
    defaults = {
        "chrome": os.path.join(local_app, "Google", "Chrome", "User Data"),
        "msedge": os.path.join(local_app, "Microsoft", "Edge", "User Data"),
        "firefox": os.path.join(roaming_app, "Mozilla", "Firefox", "Profiles"),
        "brave": os.path.join(local_app, "BraveSoftware", "Brave-Browser", "User Data"),
        "opera": os.path.join(roaming_app, "Opera Software", "Opera Stable"),
    }
    return _expand_env_path(defaults.get(browser, os.path.join(local_app, browser))) or os.path.join(local_app, browser)


def _default_browser_exe(browser: str, hint: Optional[str]) -> Optional[str]:
    expanded_hint = _expand_env_path(hint)
    if expanded_hint:
        return expanded_hint
    candidates = BROWSER_EXEC_PATHS.get(browser, [])
    for candidate in candidates:
        expanded = _expand_env_path(candidate)
        if expanded and os.path.exists(expanded):
            return expanded
    if candidates:
        return _expand_env_path(candidates[0])
    return None


def _normalize_browser_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    candidate = name.strip().lower()
    if candidate.endswith('.exe'):
        candidate = candidate[:-4]
    candidate = BROWSER_NAME_ALIASES.get(candidate, candidate)
    return candidate if candidate in BROWSER_KEYS else None


def build_restore_payload_from_logs(logs: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    apps_by_name: Dict[str, Dict[str, Any]] = {}
    browser_tabs: Dict[str, List[Dict[str, str]]] = {}
    browser_urls: Dict[str, set] = {}
    browser_exe_hint: Dict[str, str] = {}
    unique_urls = set()

    for log in logs:
        if not isinstance(log, dict):
            continue
        event_type = str(log.get('event_type') or '').lower()
        app_name = log.get('app_name')
        browser_candidate = _normalize_browser_name(log.get('browser_name')) or _normalize_browser_name(app_name)

        if event_type.startswith('app'):
            if browser_candidate:
                exe_hint = log.get('exe_path')
                if exe_hint:
                    browser_exe_hint.setdefault(browser_candidate, exe_hint)
                continue
            if not app_name:
                continue
            key = app_name.strip()
            entry = apps_by_name.setdefault(key, {
                'name': key,
                'exe': log.get('exe_path'),
                'items': [],
            })
            if not entry.get('exe') and log.get('exe_path'):
                entry['exe'] = log.get('exe_path')

        if 'browser' in event_type and 'tab' in event_type:
            if not browser_candidate:
                continue
            url = log.get('url')
            if not url:
                continue
            if not url.startswith(("http://", "https://", "file://", "chrome://", "edge://")):
                continue
            tabs_list = browser_tabs.setdefault(browser_candidate, [])
            urls_seen = browser_urls.setdefault(browser_candidate, set())
            if url in urls_seen or len(tabs_list) >= MAX_TABS_PER_BROWSER:
                continue
            title = log.get('title') or log.get('summary') or url
            tabs_list.append({'url': url, 'title': title})
            urls_seen.add(url)
            unique_urls.add(url)
            exe_hint = log.get('exe_path')
            if exe_hint:
                browser_exe_hint.setdefault(browser_candidate, exe_hint)

    apps = list(apps_by_name.values())
    browsers = []
    total_tabs = 0

    for idx, (browser, tabs) in enumerate(browser_tabs.items()):
        if not tabs:
            continue
        exe_hint = browser_exe_hint.get(browser)
        browsers.append({
            'browser': browser,
            'exe': _default_browser_exe(browser, exe_hint),
            'windows': [{
                'profile': _default_browser_profile(browser),
                'debuggingPort': 9333 + idx,
                'tabs': tabs,
            }],
        })
        total_tabs += len(tabs)

    summary = {
        'app_count': len(apps),
        'browser_count': len(browsers),
        'tab_count': total_tabs,
        'url_count': len(unique_urls),
        'app_names': sorted(apps_by_name.keys()),
        'browser_names': [entry['browser'] for entry in browsers],
    }

    payload = {
        'apps': apps,
        'browsers': browsers,
    }

    return payload, summary


def _extract_error_message(response: Optional[requests.Response]) -> str:
    if response is None:
        return "Unknown error"
    try:
        data = response.json()
    except ValueError:
        return response.text.strip() or "Unknown error"
    if isinstance(data, dict):
        return str(data.get('detail') or data.get('message') or response.text or "Unknown error")
    return response.text.strip() or "Unknown error"


def perform_restore_request(topic_label: str, payload: Dict[str, Any], summary: Dict[str, Any]) -> None:
    dry_run = st.session_state.get('restore_dry_run', True)
    request_body = {
        'apps': payload.get('apps', []),
        'browsers': payload.get('browsers', []),
        'dry_run': dry_run,
    }

    try:
        response = requests.post(
            f"{API_BASE_URL}/api/restore",
            json=request_body,
            timeout=RESTORE_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        error_message = _extract_error_message(exc.response)
        st.error(f"❌ Restore failed for {topic_label}: {error_message}")
        return
    except requests.exceptions.RequestException as exc:
        st.error(f"❌ Restore request error for {topic_label}: {exc}")
        return

    try:
        data = response.json()
    except ValueError:
        st.error("⚠️ Restore response was not valid JSON.")
        return

    summary_lines = []
    if summary.get('app_count'):
        summary_lines.append(f"- {summary['app_count']} apps")
    if summary.get('tab_count'):
        summary_lines.append(
            f"- {summary['tab_count']} browser tabs across {summary.get('browser_count', 0)} browsers"
        )
    if summary_lines:
        st.info("📋 Prepared restore payload:\n" + "\n".join(summary_lines))

    if summary.get('app_names'):
        preview_apps = ", ".join(summary['app_names'][:5])
        suffix = "…" if summary['app_count'] > 5 else ""
        st.caption(f"Apps: {preview_apps}{suffix}")
    if summary.get('browser_names'):
        st.caption("Browsers: " + ", ".join(summary['browser_names']))

    status = str(data.get('status', '')).lower()
    message = data.get('message', 'No message returned')

    if status == 'success':
        st.success(f"✅ Restore completed: {message}")
    elif status == 'dry_run':
        st.info(f"🧪 Dry run: {message}")
        missing = data.get('details', {}).get('missing_browser_executables', [])
        if missing:
            missing_lines = [
                f"- {item.get('browser')} (hint: {item.get('requested_exe') or 'unknown'})"
                for item in missing
            ]
            st.warning("⚠️ Missing browser executables:\n" + "\n".join(missing_lines))
    else:
        st.warning(f"ℹ️ Restore response: {message}")


def trigger_topic_restore(topic: str, logs: Optional[List[Dict[str, Any]]] = None, limit: int = 100) -> None:
    topic_label = topic.replace('_', ' ').title()
    if logs is None:
        topic_data = get_local_ddna_topic(topic, limit=limit)
        if topic_data.get('status') != 'success':
            message = topic_data.get('message', 'Unknown error')
            st.warning(f"⚠️ Unable to load data for {topic_label}: {message}")
            return
        logs = topic_data.get('logs', [])

    if not logs:
        st.warning(f"⚠️ No data available for {topic_label}")
        return

    payload, summary = build_restore_payload_from_logs(logs)
    if not payload.get('apps') and not payload.get('browsers'):
        st.warning(f"⚠️ No restorable items found for {topic_label}")
        return

    perform_restore_request(topic_label, payload, summary)

# Flow pipeline and workspace metadata functionality
if RESTORATION_DIR not in sys.path and os.path.exists(RESTORATION_DIR):
    sys.path.insert(0, RESTORATION_DIR)

# Restoration functionality removed - UI focuses on flow pipeline and workspace metadata only

# Import flow.py for continuous capture

# Add all necessary paths
for path in [BACKEND_DIR, FLOW_DIR, PROJECT_ROOT]:
    if path not in sys.path and os.path.exists(path):
        sys.path.insert(0, path)

try:
    from flow import handle_latest_logs
    FLOW_AVAILABLE = True
    print(f"✅ Flow module loaded successfully from: {FLOW_DIR}")
except ImportError as e:
    FLOW_AVAILABLE = False
    handle_latest_logs = None
    print(f"⚠️ Flow module not available: {e}")
    print(f"   Tried path: {FLOW_DIR}")

# Import sync function from server
try:
    from server import sync_workspace_to_cloud_direct
    SYNC_AVAILABLE = True
    print(f"✅ Sync function loaded successfully from backend/server.py")
except ImportError as e:
    SYNC_AVAILABLE = False
    sync_workspace_to_cloud_direct = None
    print(f"⚠️ Sync function not available: {e}")
    print(f"   Make sure backend/server.py is accessible")

# Import custom topic functions from server
try:
    from server import add_custom_topic_direct, get_all_topics_direct
    CUSTOM_TOPICS_AVAILABLE = True
    print(f"✅ Custom topic functions loaded successfully from backend/server.py")
except ImportError as e:
    CUSTOM_TOPICS_AVAILABLE = False
    add_custom_topic_direct = None
    get_all_topics_direct = None
    print(f"⚠️ Custom topic functions not available: {e}")
    print(f"   Make sure backend/server.py is accessible")

# Remote API configuration
REMOTE_API_BASE_URL = "https://intellios-database.onrender.com"

def sync_workspace_to_remote(username: str, workspace_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sync workspace data to remote DDNA database via direct function call
    
    Args:
        username: Username for the workspace
        workspace_name: Name of the workspace
        state: State data containing apps, browsers, etc.
        
    Returns:
        Response dict with status and message
    """
    if not SYNC_AVAILABLE or sync_workspace_to_cloud_direct is None:
        return {
            "status": "error",
            "message": "Sync function not available. Make sure backend modules are accessible."
        }
    
    try:
        # Directly call the backend function (no HTTP overhead)
        result = sync_workspace_to_cloud_direct(username, workspace_name, state)
        return result
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error calling sync function: {str(e)}"
        }

# Load workspaces from JSON file
WORKSPACES_PATH = os.path.join(os.path.dirname(__file__), "workspaces.json")
if os.path.exists(WORKSPACES_PATH):
    with open(WORKSPACES_PATH) as f:
        workspaces = json.load(f)
else:
    workspaces = []

lastRestored = next((ws for ws in workspaces if ws.get('lastRestored')), None)
aiSuggestion = "Based on your usage pattern, I recommend creating a 'Research Mode' workspace for your frequent article reading sessions."
# Use hardcoded username for demo
username = "jatan_demo"

# Initialize session state for continuous capture
if 'capture_running' not in st.session_state:
    st.session_state.capture_running = False
if 'capture_count' not in st.session_state:
    st.session_state.capture_count = 0
if 'last_capture_time' not in st.session_state:
    st.session_state.last_capture_time = None
if 'capture_thread' not in st.session_state:
    st.session_state.capture_thread = None
if 'recent_captures' not in st.session_state:
    st.session_state.recent_captures = []
if 'last_capture_result' not in st.session_state:
    st.session_state.last_capture_result = None
if 'restore_dry_run' not in st.session_state:
    st.session_state.restore_dry_run = True

def run_continuous_capture(delay=5):
    """Run capture continuously in background"""
    while st.session_state.capture_running:
        try:
            if FLOW_AVAILABLE and handle_latest_logs:
                result = handle_latest_logs(
                    workspace_name=f'live_capture_{st.session_state.capture_count}'
                )
                st.session_state.capture_count += 1
                st.session_state.last_capture_time = datetime.datetime.now().strftime('%H:%M:%S')
                
                # Store the result for display
                st.session_state.last_capture_result = result
                
                # Extract logs with scores for recent captures list
                if result.get('status') == 'success' and result.get('logs'):
                    logs = result.get('logs', [])
                    capture_summary = {
                        'time': st.session_state.last_capture_time,
                        'count': st.session_state.capture_count,
                        'n_logs': len(logs),
                        'logs_with_scores': [
                            {
                                'event_type': log.get('event_type', 'Unknown'),
                                'summary': log.get('summary', 'No summary')[:60] + '...' if len(log.get('summary', '')) > 60 else log.get('summary', 'No summary'),
                                'score': log.get('similarity', 0.0),
                                'topics': [t.get('topic') for t in log.get('topic_matches', [])[:2]]  # Top 2 topics
                            }
                            for log in logs[:5]  # Only keep last 5 logs
                        ]
                    }
                    # Keep only last 10 captures
                    st.session_state.recent_captures.insert(0, capture_summary)
                    if len(st.session_state.recent_captures) > 10:
                        st.session_state.recent_captures = st.session_state.recent_captures[:10]
            time.sleep(delay)
        except Exception as e:
            print(f"Capture error: {e}")
            break

st.set_page_config(page_title="Adaptive Workspace Dashboard", layout="wide")

# Top Bar
col_left, col_right = st.columns([3, 1])

with col_left:
    st.markdown(f"""
    <div style='background: linear-gradient(to right, #2d2d44, #3f8dfc); padding: 1.5rem; border-radius: 1rem; margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center;'>
      <div style='display: flex; align-items: center; gap: 1rem;'>
        <span style='font-size:2rem; color:#b086f2;'>🧠</span>
        <span style='font-size:1.5rem; font-weight:bold; background: linear-gradient(to right, #b086f2, #3f8dfc); -webkit-background-clip: text; color: transparent;'>Adaptive Workspace</span>
      </div>
      <div style='display: flex; align-items: center; gap: 1rem;'>
        <span style='color:#ccc;'>Welcome back, {username}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

with col_right:
    st.markdown("### 🎬 Live Capture")
    
    if FLOW_AVAILABLE:
        # Capture status indicator
        if st.session_state.capture_running:
            st.markdown("""
            <div style='background: linear-gradient(to right, #00ff88, #00d4aa); padding: 0.5rem; border-radius: 0.5rem; text-align: center; margin-bottom: 0.5rem;'>
                <span style='color: white; font-weight: bold;'>🔴 LIVE</span>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style='background: linear-gradient(to right, #555, #777); padding: 0.5rem; border-radius: 0.5rem; text-align: center; margin-bottom: 0.5rem;'>
                <span style='color: white; font-weight: bold;'>⚫ STOPPED</span>
            </div>
            """, unsafe_allow_html=True)
        
        # Control buttons
        col_btn1, col_btn2 = st.columns(2)
        
        with col_btn1:
            if st.button("▶️ Start", disabled=st.session_state.capture_running, use_container_width=True, key="start_capture"):
                st.session_state.capture_running = True
                st.session_state.capture_count = 0
                thread = threading.Thread(target=run_continuous_capture, args=(5,), daemon=True)
                thread.start()
                st.session_state.capture_thread = thread
                st.rerun()
        
        with col_btn2:
            if st.button("⏹️ Stop", disabled=not st.session_state.capture_running, use_container_width=True, key="stop_capture"):
                st.session_state.capture_running = False
                st.rerun()
        
        # Manual capture button
        if st.button("📸 Capture Once", use_container_width=True, key="manual_capture", 
                    type="primary", disabled=st.session_state.capture_running):
            with st.spinner("Capturing..."):
                try:
                    result = handle_latest_logs(
                        workspace_name=f'manual_capture_{int(time.time())}'
                    )
                    if result.get('status') == 'success':
                        st.success(f"✅ Captured {result.get('n_logs', 0)} logs!")
                        st.session_state.capture_count += 1
                        st.session_state.last_capture_time = datetime.datetime.now().strftime('%H:%M:%S')
                        st.session_state.last_capture_result = result
                        
                        # Store logs with scores
                        if result.get('logs'):
                            logs = result.get('logs', [])
                            capture_summary = {
                                'time': st.session_state.last_capture_time,
                                'count': st.session_state.capture_count,
                                'n_logs': len(logs),
                                'logs_with_scores': [
                                    {
                                        'event_type': log.get('event_type', 'Unknown'),
                                        'summary': log.get('summary', 'No summary')[:60] + '...' if len(log.get('summary', '')) > 60 else log.get('summary', 'No summary'),
                                        'score': log.get('similarity', 0.0),
                                        'topics': [t.get('topic') for t in log.get('topic_matches', [])[:2]]
                                    }
                                    for log in logs[:5]
                                ]
                            }
                            st.session_state.recent_captures.insert(0, capture_summary)
                            if len(st.session_state.recent_captures) > 10:
                                st.session_state.recent_captures = st.session_state.recent_captures[:10]
                        
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"❌ Capture failed: {result.get('message', 'Unknown error')}")
                except Exception as e:
                    st.error(f"❌ Error: {str(e)}")
        
        # Stats
        if st.session_state.capture_count > 0:
            st.metric("Total Captures", st.session_state.capture_count)
            if st.session_state.last_capture_time:
                st.caption(f"⏰ Last: {st.session_state.last_capture_time}")
    else:
        st.error("⚠️ Flow module not available")
        st.caption(f"Flow directory: {FLOW_DIR}")
        st.caption(f"Exists: {os.path.exists(FLOW_DIR)}")
        if st.button("🔄 Retry Import", key="retry_import"):
            st.rerun()


# Dashboard Overview
col1, col2 = st.columns(2)
with col1:
    st.subheader("Dashboard Overview")
    if lastRestored:
        st.success(f"Last restored: {lastRestored['name']}")
        st.write(f"{lastRestored['apps'].__len__()} apps • {lastRestored['files']} files • {lastRestored['tabs']} browser tabs")
        st.write(f"{lastRestored['lastRestored']}")
    else:
        st.info("No workspace restored yet.")
with col2:
    st.markdown(f"""
    <div style='background: linear-gradient(to right, #b086f2, #3f8dfc); padding: 1rem; border-radius: 0.75rem;'>
      <span style='font-size:1.2rem;'>⚡</span>
      <span style='color:#fff; font-weight:500;'>AI Suggestion</span>
      <div style='color:#eee; margin-top:0.5rem;'>{aiSuggestion}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# Recent Captures Window - Show latest capture output
if FLOW_AVAILABLE and st.session_state.recent_captures:
    st.markdown("### 📊 Recent Capture Activity")
    
    # Show the most recent capture in detail
    latest = st.session_state.recent_captures[0]
    
    st.markdown(f"""
    <div style='
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px;
        padding: 1rem;
        margin-bottom: 1rem;
    '>
        <div style='display: flex; justify-content: space-between; align-items: center;'>
            <span style='color: white; font-size: 1.2rem; font-weight: bold;'>
                Capture #{latest['count']} at {latest['time']}
            </span>
            <span style='
                background: rgba(255, 255, 255, 0.2);
                padding: 0.25rem 0.75rem;
                border-radius: 20px;
                color: white;
                font-weight: bold;
            '>
                {latest['n_logs']} logs
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Display logs with their matching scores
    for idx, log_info in enumerate(latest['logs_with_scores']):
        score = log_info['score']
        
        # Color based on score
        if score >= 0.75:
            color = "#4ade80"  # Green
            score_label = "High Match"
        elif score >= 0.5:
            color = "#fbbf24"  # Yellow
            score_label = "Medium Match"
        else:
            color = "#94a3b8"  # Gray
            score_label = "Low Match"
        
        # Topics display
        topics_str = ", ".join(log_info['topics']) if log_info['topics'] else "No topics"
        
        st.markdown(f"""
        <div style='
            background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
            border-left: 4px solid {color};
            border-radius: 8px;
            padding: 0.75rem;
            margin-bottom: 0.5rem;
        '>
            <div style='display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.5rem;'>
                <div style='flex: 1;'>
                    <div style='color: #1e293b; font-weight: 600; margin-bottom: 0.25rem;'>
                        {log_info['event_type']}
                    </div>
                    <div style='color: #475569; font-size: 0.9rem;'>
                        {log_info['summary']}
                    </div>
                    <div style='color: #64748b; font-size: 0.8rem; margin-top: 0.25rem;'>
                        🏷️ {topics_str}
                    </div>
                </div>
                <div style='
                    background: {color};
                    color: white;
                    padding: 0.25rem 0.75rem;
                    border-radius: 20px;
                    font-size: 0.85rem;
                    font-weight: bold;
                    white-space: nowrap;
                    margin-left: 1rem;
                '>
                    {score:.2f} - {score_label}
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    # Show history in an expander
    if len(st.session_state.recent_captures) > 1:
        with st.expander(f"📜 View Previous Captures ({len(st.session_state.recent_captures) - 1} more)", expanded=False):
            for capture in st.session_state.recent_captures[1:]:
                st.markdown(f"""
                <div style='
                    background: #f8fafc;
                    border-radius: 6px;
                    padding: 0.5rem;
                    margin-bottom: 0.5rem;
                '>
                    <div style='color: #1e293b; font-weight: 600;'>
                        Capture #{capture['count']} at {capture['time']} - {capture['n_logs']} logs
                    </div>
                    <div style='color: #64748b; font-size: 0.85rem; margin-top: 0.25rem;'>
                        Avg Score: {sum(log['score'] for log in capture['logs_with_scores']) / len(capture['logs_with_scores']):.2f}
                    </div>
                </div>
                """, unsafe_allow_html=True)

st.markdown("---")

# Local DDNA Section
st.subheader("🧬 Local DDNA Activity Monitoring")

dry_run_mode = st.checkbox(
    "Dry-run restore (preview only)",
    value=st.session_state.get('restore_dry_run', True),
    key="restore_dry_run",
)
if dry_run_mode:
    st.caption("Restore requests will run in dry-run mode and report missing executables.")

# Get DDNA stats
ddna_stats = get_local_ddna_stats()

if ddna_stats.get("status") == "success":
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Total Topics", ddna_stats.get("topics", 0))
    with col2:
        st.metric("Total Logs", ddna_stats.get("total_logs", 0))
    with col3:
        timestamp_range = ddna_stats.get("timestamp_range", {})
        if timestamp_range.get("latest"):
            st.metric("Last Activity", timestamp_range["latest"][:19])
        else:
            st.metric("Last Activity", "N/A")
    
    # Display topics
    st.subheader("Available Topics")
    topics_response = get_local_ddna_topics()
    
    if topics_response.get("status") == "success" and topics_response.get("count", 0) > 0:
        topics = topics_response.get("topics", [])
        logs_by_topic = ddna_stats.get("logs_by_topic", {})
        
        # Icon mapping for different topics
        topic_icons = {
            "application_lifecycle": "🚀",
            "browser_activity": "🌐",
            "system_startup": "⚡",
            "system_shutdown": "🔌",
            "user_sessions": "👤",
            "network_activity": "📡",
            "disk_activity": "💾",
            "performance_issues": "⚠️",
            "security": "🔒",
            "updates": "🔄",
            "application_errors": "❌",
            "system_errors": "🚨",
            "hardware_events": "🖥️",
            "service_operations": "⚙️",
            "driver_operations": "🔧",
            "maintenance": "🛠️",
            "web_development": "💻",
            "extracurricular": "🎯"
        }
        
        # Create a grid of topics with cards
        cols_per_row = 3
        for i in range(0, len(topics), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, col in enumerate(cols):
                if i + j < len(topics):
                    topic = topics[i + j]
                    log_count = logs_by_topic.get(topic, 0)
                    icon = topic_icons.get(topic, "🎯")
                    
                    with col:
                        # Create a card-like container
                        st.markdown(f"""
                        <div style='
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            border-radius: 12px;
                            padding: 1.5rem;
                            margin-bottom: 1rem;
                            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                            transition: transform 0.2s;
                            cursor: pointer;
                        '>
                            <div style='display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem;'>
                                <span style='font-size: 2rem;'>{icon}</span>
                                <span style='color: white; font-weight: 600; font-size: 1.1rem;'>
                                    {topic.replace('_', ' ').title()}
                                </span>
                            </div>
                            <div style='color: rgba(255, 255, 255, 0.9); font-size: 0.9rem;'>
                                <span style='
                                    background: rgba(255, 255, 255, 0.2);
                                    padding: 0.25rem 0.75rem;
                                    border-radius: 20px;
                                    display: inline-block;
                                '>
                                    {log_count} logs
                                </span>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # Buttons row
                        btn_col1, btn_col2 = st.columns(2)
                        
                        with btn_col1:
                            if st.button(f"View Details", key=f"view_{topic}", use_container_width=True):
                                st.session_state['selected_topic'] = topic
                        
                        with btn_col2:
                            if st.button(f"🔄 Restore", key=f"restore_{topic}", use_container_width=True, type="primary"):
                                with st.spinner(f"Restoring {topic.replace('_', ' ').title()}..."):
                                    trigger_topic_restore(topic, limit=150)
                        
                        # Sync with Cloud button (full width)
                        if st.button(f"☁️ Sync with Cloud", key=f"sync_{topic}", use_container_width=True):
                            with st.spinner(f"Syncing {topic.replace('_', ' ').title()} to cloud..."):
                                # Get topic data
                                topic_data = get_local_ddna_topic(topic, limit=100)
                                
                                if topic_data.get('status') == 'success':
                                    logs = topic_data.get('logs', [])
                                    
                                    # Build state from topic logs
                                    apps_data = []
                                    browsers_data = []
                                    
                                    # Extract unique apps
                                    apps_seen = set()
                                    for log in logs:
                                        if log.get('event_type', '').startswith('app') and log.get('app_name'):
                                            app_name = log.get('app_name')
                                            if app_name not in apps_seen:
                                                apps_seen.add(app_name)
                                                apps_data.append({
                                                    'name': app_name,
                                                    'exe': log.get('exe_path'),
                                                    'pid': log.get('pid'),
                                                    'items': []
                                                })
                                    
                                    # Extract browser tabs
                                    browser_tabs = {}
                                    for log in logs:
                                        if 'browser' in log.get('event_type', '') and 'tab' in log.get('event_type', ''):
                                            browser_name = log.get('browser_name', 'chrome')
                                            if browser_name not in browser_tabs:
                                                browser_tabs[browser_name] = []
                                            
                                            if log.get('url'):
                                                browser_tabs[browser_name].append({
                                                    'url': log.get('url'),
                                                    'title': log.get('title', log.get('summary', 'Untitled')),
                                                    'description': log.get('summary', '')
                                                })
                                    
                                    # Build browsers data
                                    for idx, (browser_name, tabs) in enumerate(browser_tabs.items()):
                                        if tabs:
                                            browsers_data.append({
                                                'browser': browser_name,
                                                'exe': None,
                                                'windows': [{
                                                    'profile': 'Default',
                                                    'debuggingPort': 9222 + idx,
                                                    'tabs': tabs[:50]
                                                }]
                                            })
                                    
                                    state_data = {
                                        "saved_at": datetime.datetime.now().isoformat(),
                                        "user": username,
                                        "apps": apps_data,
                                        "browsers": browsers_data,
                                        "summary": f"Topic: {topic} - {len(apps_data)} apps, {sum(len(b['windows'][0]['tabs']) for b in browsers_data)} tabs"
                                    }
                                    
                                    result = sync_workspace_to_remote(
                                        username=username,
                                        workspace_name=f"topic_{topic}",
                                        state=state_data
                                    )
                                    
                                    if result['status'] == 'success':
                                        st.success(f"✅ {result['message']}")
                                        time.sleep(1)
                                        st.rerun()
                                    else:
                                        st.error(f"❌ Sync failed: {result['message']}")
                                else:
                                    st.warning(f"⚠️ No data available for {topic}")
    
    # Display selected topic details
    if 'selected_topic' in st.session_state:
        selected_topic = st.session_state['selected_topic']
        st.markdown("---")
        st.markdown(f"""
        <div style='
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 12px;
            padding: 1.5rem;
            margin: 1rem 0;
        '>
            <h2 style='color: white; margin: 0;'>
                {topic_icons.get(selected_topic, "🎯")} {selected_topic.replace('_', ' ').title()}
            </h2>
        </div>
        """, unsafe_allow_html=True)
        
        topic_data = get_local_ddna_topic(selected_topic, limit=20)
        
        if topic_data.get("status") == "success":
            # Action buttons row
            action_col1, action_col2, action_col3 = st.columns([2, 1, 1])
            
            with action_col1:
                st.write(f"📊 Showing **{topic_data.get('count', 0)}** of **{topic_data.get('total', 0)}** logs")
            
            with action_col2:
                if st.button("🔄 Restore Topic", key=f"restore_detail_{selected_topic}", type="primary", use_container_width=True):
                    with st.spinner(f"Restoring {selected_topic.replace('_', ' ').title()}..."):
                        trigger_topic_restore(selected_topic, limit=200)
            
            with action_col3:
                if st.button("❌ Close", key=f"close_{selected_topic}", use_container_width=True):
                    del st.session_state['selected_topic']
                    st.rerun()
            
            st.markdown("---")
            
            logs = topic_data.get("logs", [])
            
            # Create cards for each log entry
            for idx, log in enumerate(logs):
                # Determine card color based on event type
                event_type = log.get('event_type', 'Unknown')
                if 'error' in event_type.lower():
                    gradient = "linear-gradient(135deg, #f093fb 0%, #f5576c 100%)"
                    emoji = "❌"
                elif 'start' in event_type.lower() or 'launch' in event_type.lower():
                    gradient = "linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)"
                    emoji = "🚀"
                elif 'close' in event_type.lower() or 'end' in event_type.lower():
                    gradient = "linear-gradient(135deg, #fa709a 0%, #fee140 100%)"
                    emoji = "🔚"
                else:
                    gradient = "linear-gradient(135deg, #a8edea 0%, #fed6e3 100%)"
                    emoji = "📝"
                
                # Format timestamp
                timestamp = log.get('timestamp', log.get('saved_at', 'N/A'))
                if timestamp != 'N/A':
                    try:
                        timestamp = timestamp[:19].replace('T', ' ')
                    except:
                        pass
                
                # Build card content
                st.markdown(f"""
                <div style='
                    background: {gradient};
                    border-radius: 10px;
                    padding: 1.25rem;
                    margin-bottom: 1rem;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                '>
                    <div style='display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.75rem;'>
                        <div style='display: flex; align-items: center; gap: 0.5rem;'>
                            <span style='font-size: 1.5rem;'>{emoji}</span>
                            <span style='color: #333; font-weight: 600; font-size: 1.1rem;'>
                                {event_type}
                            </span>
                        </div>
                        <span style='
                            background: rgba(255, 255, 255, 0.7);
                            padding: 0.25rem 0.75rem;
                            border-radius: 15px;
                            font-size: 0.85rem;
                            color: #555;
                        '>
                            🕐 {timestamp}
                        </span>
                    </div>
                    <div style='color: #333; margin-bottom: 0.5rem;'>
                        <strong>Summary:</strong> {log.get('summary', 'No summary available')}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Additional details in an expander
                with st.expander(f"🔍 View Full Details - Log #{idx + 1}", expanded=False):
                    details_cols = st.columns(2)
                    
                    # Filter out already displayed fields
                    detail_fields = {k: v for k, v in log.items() 
                                   if k not in ['event_type', 'summary', 'timestamp', 'saved_at'] 
                                   and v is not None}
                    
                    # Display key-value pairs in columns
                    items = list(detail_fields.items())
                    mid = len(items) // 2 + len(items) % 2
                    
                    with details_cols[0]:
                        for key, value in items[:mid]:
                            st.markdown(f"**{key.replace('_', ' ').title()}:** `{value}`")
                    
                    with details_cols[1]:
                        for key, value in items[mid:]:
                            st.markdown(f"**{key.replace('_', ' ').title()}:** `{value}`")
                    
                    # Show raw JSON as fallback
                    with st.expander("📄 Raw JSON", expanded=False):
                        st.json(log)
        else:
            st.error(f"Error loading topic: {topic_data.get('message', 'Unknown error')}")
    
    # Custom Topic Creation Section
    st.markdown("---")
    st.subheader("➕ Create Custom Topic")
    
    if CUSTOM_TOPICS_AVAILABLE and add_custom_topic_direct is not None:
        with st.expander("🎨 Add Your Own Topic", expanded=False):
            st.markdown("""
            Create a custom topic to classify your activities. The system will use your description 
            to automatically match logs to this topic using AI similarity matching.
            """)
            
            # Topic creation form
            with st.form("custom_topic_form"):
                topic_name = st.text_input(
                    "Topic Name",
                    placeholder="e.g., game_development, cybersecurity, video_editing",
                    help="Use lowercase with underscores. Must be unique."
                )
                
                description = st.text_area(
                    "Description",
                    placeholder="Describe what this topic represents. Be specific and detailed.\n\nExample: Developing and playing video games. Includes game engines like Unity or Unreal, game design tools, Steam, gaming platforms, and game development tutorials.",
                    help="A detailed description helps the AI better match logs to this topic.",
                    height=150
                )
                
                examples = st.text_area(
                    "Examples (Optional)",
                    placeholder="Add example text that represents this topic, one per line.\n\nExample:\nApp: Unity.exe | Window: MyGame - Unity Editor\nTab: Unreal Engine Documentation\nTab: Steam Store",
                    help="Optional: Provide examples of logs/activities that belong to this topic (one per line).",
                    height=100
                )
                
                col1, col2 = st.columns([3, 1])
                with col1:
                    submit_button = st.form_submit_button("Create Topic", use_container_width=True, type="primary")
                with col2:
                    if st.form_submit_button("Clear", use_container_width=True):
                        st.rerun()
                
                if submit_button:
                    if not topic_name or not description:
                        st.error("⚠️ Topic name and description are required!")
                    else:
                        # Parse examples
                        example_list = []
                        if examples:
                            example_list = [line.strip() for line in examples.split('\n') if line.strip()]
                        
                        with st.spinner(f"Creating topic '{topic_name}'..."):
                            # Call the direct function (no HTTP overhead)
                            result = add_custom_topic_direct(
                                topic_name=topic_name,
                                description=description,
                                examples=example_list
                            )
                            
                            if result.get('status') == 'success':
                                st.success(f"✅ {result.get('message')}")
                                
                                # Show JSON file creation status
                                if result.get('json_file_created'):
                                    st.info(f"📁 Created JSON file: {result.get('json_file_path')}")
                                else:
                                    st.warning(f"⚠️ Topic added to vector DB, but JSON file was not created")
                                
                                st.balloons()
                                time.sleep(1)
                                # Force UI refresh to show the new topic
                                st.rerun()
                            else:
                                st.error(f"❌ {result.get('message')}")
        
        # Display all topics (including custom ones)
        if st.checkbox("Show All Topics (Including Custom)", value=False):
            with st.spinner("Loading all topics..."):
                all_topics_result = get_all_topics_direct()
                
                if all_topics_result.get('status') == 'success':
                    all_topics = all_topics_result.get('topics', {})
                    
                    # Separate predefined and custom topics
                    predefined_topics = {k: v for k, v in all_topics.items() if not v.get('custom', False)}
                    custom_topics = {k: v for k, v in all_topics.items() if v.get('custom', False)}
                    
                    st.markdown(f"**Total Topics:** {all_topics_result.get('total_count', 0)} "
                              f"({len(predefined_topics)} predefined + {len(custom_topics)} custom)")
                    
                    if custom_topics:
                        st.markdown("### 🎨 Custom Topics")
                        for topic_name, topic_info in custom_topics.items():
                            with st.expander(f"🔹 {topic_name}", expanded=False):
                                st.markdown(f"**Description:** {topic_info.get('description', 'No description')}")
                                st.caption("This is a user-defined custom topic")
                    
                    if predefined_topics:
                        st.markdown("### 📚 Predefined Topics")
                        for topic_name, topic_info in predefined_topics.items():
                            with st.expander(f"🔸 {topic_name}", expanded=False):
                                st.markdown(f"**Description:** {topic_info.get('description', 'No description')}")
                else:
                    st.error(f"Failed to load topics: {all_topics_result.get('message', 'Unknown error')}")
    else:
        st.info("ℹ️ Custom topic creation is not available. Make sure the backend server is running.")
    
    # Search functionality
    st.markdown("---")
    st.subheader("🔍 Search Local DDNA")
    
    search_col1, search_col2 = st.columns([3, 1])
    with search_col1:
        search_query = st.text_input("Enter search query", key="search_query")
    with search_col2:
        case_sensitive = st.checkbox("Case Sensitive", key="case_sensitive")
    
    if st.button("Search") and search_query:
        search_results = search_local_ddna(search_query, case_sensitive=case_sensitive)
        
        if search_results.get("status") == "success":
            st.success(f"🎯 Found **{search_results.get('total_matches', 0)}** matches across **{search_results.get('matched_topics', 0)}** topics")
            
            results = search_results.get("results", {})
            for topic, matches in results.items():
                st.markdown(f"""
                <div style='
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    border-radius: 10px;
                    padding: 1rem;
                    margin: 1rem 0 0.5rem 0;
                '>
                    <h3 style='color: white; margin: 0;'>
                        {topic_icons.get(topic, "🎯")} {topic.replace('_', ' ').title()} 
                        <span style='
                            background: rgba(255, 255, 255, 0.2);
                            padding: 0.25rem 0.75rem;
                            border-radius: 20px;
                            font-size: 0.9rem;
                            margin-left: 0.5rem;
                        '>
                            {len(matches)} matches
                        </span>
                    </h3>
                </div>
                """, unsafe_allow_html=True)
                
                # Show matches in card format
                for idx, match in enumerate(matches[:5]):  # Show first 5 matches per topic
                    event_type = match.get('event_type', 'Unknown')
                    timestamp = match.get('timestamp', match.get('saved_at', 'N/A'))
                    if timestamp != 'N/A':
                        try:
                            timestamp = timestamp[:19].replace('T', ' ')
                        except:
                            pass
                    
                    st.markdown(f"""
                    <div style='
                        background: linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%);
                        border-radius: 8px;
                        padding: 1rem;
                        margin-bottom: 0.75rem;
                        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                    '>
                        <div style='display: flex; justify-content: space-between; align-items: start;'>
                            <div>
                                <span style='color: #333; font-weight: 600;'>{event_type}</span>
                                <div style='color: #555; font-size: 0.9rem; margin-top: 0.25rem;'>
                                    {match.get('summary', 'No summary available')}
                                </div>
                            </div>
                            <span style='
                                background: rgba(255, 255, 255, 0.7);
                                padding: 0.25rem 0.5rem;
                                border-radius: 12px;
                                font-size: 0.8rem;
                                color: #555;
                                white-space: nowrap;
                            '>
                                {timestamp}
                            </span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    with st.expander(f"View Details - Match #{idx + 1}", expanded=False):
                        st.json(match)
                
                if len(matches) > 5:
                    st.info(f"... and **{len(matches) - 5}** more matches in this topic")
                
                st.markdown("<br>", unsafe_allow_html=True)
        else:
            st.warning(search_results.get("message", "No results found"))
    
else:
    st.warning(f"⚠️ {ddna_stats.get('message', 'Unable to load Local DDNA data')}")
    st.info("Make sure the Local DDNA directory exists and contains data files.")

# Auto-refresh when capture is running
if st.session_state.capture_running:
    st.markdown("---")
    st.markdown("""
    <div style='
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
        margin-top: 2rem;
    '>
        <span style='color: white; font-size: 1.2rem; font-weight: bold;'>
            🔄 Auto-refreshing every 5 seconds...
        </span>
    </div>
    """, unsafe_allow_html=True)
    time.sleep(5)
    st.rerun()
