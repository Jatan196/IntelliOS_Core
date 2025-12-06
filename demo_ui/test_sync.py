"""
Test script for workspace sync functionality
"""
import json
import requests
from typing import Dict, Any

# Remote API configuration
REMOTE_API_BASE_URL = "https://intellios-database.onrender.com"

def sync_workspace_to_remote(username: str, workspace_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sync workspace data to remote DDNA database
    
    Args:
        username: Username for the workspace
        workspace_name: Name of the workspace
        state: State data containing apps, browsers, etc.
        
    Returns:
        Response dict with status and message
    """
    try:
        url = f"{REMOTE_API_BASE_URL}/api/workspace"
        payload = {
            "username": username,
            "workspace_name": workspace_name,
            "state": state
        }
        
        print(f"📤 Sending request to: {url}")
        print(f"📦 Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(url, json=payload, timeout=10)
        
        print(f"📥 Response status: {response.status_code}")
        print(f"📥 Response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            result = response.json()
            return {
                "status": "success",
                "message": result.get("message", "Workspace synced successfully"),
                "response": result
            }
        else:
            return {
                "status": "error",
                "message": f"API returned status {response.status_code}: {response.text}"
            }
    except requests.exceptions.Timeout:
        return {
            "status": "error",
            "message": "Request timed out. The remote server might be slow or unavailable."
        }
    except requests.exceptions.ConnectionError:
        return {
            "status": "error",
            "message": "Could not connect to remote server. Check your internet connection."
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)}"
        }


if __name__ == "__main__":
    # Load actual workspace data from workspaces.json
    import os
    
    workspaces_path = os.path.join(os.path.dirname(__file__), "workspaces.json")
    if os.path.exists(workspaces_path):
        with open(workspaces_path) as f:
            workspaces = json.load(f)
        
        # Test with the first workspace
        if workspaces:
            ws = workspaces[0]
            test_username = os.environ.get('USERNAME') or "TestUser"
            test_workspace = ws['name']
            test_state = ws.get('state', {})
            
            print(f"🧪 Testing workspace sync to remote DDNA...")
            print(f"📋 Workspace: {test_workspace}")
            print(f"👤 Username: {test_username}")
            print("=" * 60)
            
            # Show state structure summary
            if test_state:
                print(f"📊 State Summary:")
                print(f"   - Apps: {len(test_state.get('apps', []))}")
                print(f"   - Browsers: {len(test_state.get('browsers', []))}")
                if test_state.get('apps'):
                    print(f"   - App Details:")
                    for app in test_state['apps']:
                        print(f"     • {app.get('name')}: {app.get('exe')}")
                        if app.get('items'):
                            print(f"       Files: {', '.join(app['items'])}")
                if test_state.get('browsers'):
                    print(f"   - Browser Details:")
                    for browser in test_state['browsers']:
                        print(f"     • {browser.get('browser')}")
                        for window in browser.get('windows', []):
                            tabs = window.get('tabs', [])
                            print(f"       Tabs ({len(tabs)}):")
                            for tab in tabs[:3]:  # Show first 3 tabs
                                print(f"         - {tab.get('title')}: {tab.get('url')}")
            
            print("=" * 60)
            
            result = sync_workspace_to_remote(test_username, test_workspace, test_state)
            
            print("=" * 60)
            print(f"✅ Result: {result['status']}")
            print(f"📝 Message: {result['message']}")
            
            if result['status'] == 'success':
                print("\n✅ Test PASSED - Workspace synced successfully!")
                print("📊 Detailed state data was sent including:")
                print("   ✓ Full app information (exe paths, PIDs, items)")
                print("   ✓ Browser tabs with URLs and titles")
                print("   ✓ File paths and window states")
            else:
                print(f"\n❌ Test FAILED - {result['message']}")
        else:
            print("❌ No workspaces found in workspaces.json")
    else:
        print(f"❌ workspaces.json not found at {workspaces_path}")
