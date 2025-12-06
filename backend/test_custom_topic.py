"""
Test script for custom topic creation with JSON file generation
"""
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import add_custom_topic_direct

print("=" * 60)
print("Testing Custom Topic Creation")
print("=" * 60)

# Test topic data
test_topic_name = "test_gaming"
test_description = "Playing and developing video games. Includes Steam, Unity, Unreal Engine, and gaming platforms."
test_examples = [
    "App: Steam.exe | Window: Steam Client",
    "Tab: Steam Store | URL: https://store.steampowered.com",
    "App: Unity.exe | Window: Game Project - Unity Editor"
]

print(f"\n📝 Creating test topic: '{test_topic_name}'")
print(f"Description: {test_description}")
print(f"Examples: {len(test_examples)} provided")

# Call the function
result = add_custom_topic_direct(
    topic_name=test_topic_name,
    description=test_description,
    examples=test_examples
)

print("\n" + "=" * 60)
print("Result:")
print("=" * 60)
print(f"Status: {result.get('status')}")
print(f"Message: {result.get('message')}")

if result.get('status') == 'success':
    print("\n✅ Topic created successfully!")
    
    # Check JSON file creation status from result
    if result.get('json_file_created'):
        print(f"✅ JSON file created: {result.get('json_file_path')}")
        
        # Read and display file content
        import json
        json_file = result.get('json_file_path')
        with open(json_file, 'r', encoding='utf-8') as f:
            content = json.load(f)
        print(f"📄 File content: {content}")
        print(f"   (Empty array ready for logs)")
    else:
        print(f"⚠️ JSON file was not created")
        if result.get('json_file_path'):
            print(f"   Expected path: {result.get('json_file_path')}")
    
    # Display topic details
    if result.get('topic'):
        topic = result['topic']
        print(f"\n📋 Topic Details:")
        print(f"   Name: {topic.get('name')}")
        print(f"   Description: {topic.get('description')}")
        print(f"   Examples: {len(topic.get('examples', []))}")
else:
    print(f"\n❌ Failed to create topic: {result.get('message')}")

print("\n" + "=" * 60)
print("Test completed!")
print("=" * 60)
