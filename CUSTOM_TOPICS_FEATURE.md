# Custom Topic Creation Feature

## Overview
Added functionality to create user-defined custom topics that integrate with the vector database for automatic log classification. The system uses AI similarity matching to classify logs against both predefined and custom topics.

## Implementation Summary

### 1. Vector Database (`backend/vector_db.py`)

#### New Methods Added:

**`add_custom_topic(topic_name, description, examples)`**
- Adds a new user-defined topic to the topics collection
- Validates uniqueness and sanitizes topic names
- Creates embeddings from description + examples
- Updates in-memory topic embeddings cache
- Marks topic as custom in metadata

**`get_all_topics()`**
- Returns all topics (predefined + custom)
- Includes metadata about whether each topic is custom or predefined
- Provides total count and status information

### 2. Backend Server (`backend/server.py`)

#### New Direct Functions (for zero-latency programmatic access):

**`add_custom_topic_direct(topic_name, description, examples)`**
- Directly callable function (no HTTP overhead)
- Validates vector DB availability
- Calls `vector_db.add_custom_topic()`
- Returns dict with status/message/topic details

**`get_all_topics_direct()`**
- Directly callable function (no HTTP overhead)
- Calls `vector_db.get_all_topics()`
- Returns dict with status/topics/total_count

#### New HTTP Endpoints (for API consumers):

**`POST /api/topics/custom`**
- Request body: `{topic_name, description, examples[]}`
- Response: `{status, message, topic{name, description, examples}}`
- Thin wrapper around `add_custom_topic_direct()`

**`GET /api/topics/all`**
- Returns all topics with their descriptions and custom flag
- Response: `{status, topics{}, total_count}`
- Thin wrapper around `get_all_topics_direct()`

### 3. Demo UI (`demo_ui/ui.py`)

#### New Imports:
```python
from server import add_custom_topic_direct, get_all_topics_direct
```

#### New UI Section: "Create Custom Topic"
Located after the topics display, before the search functionality.

**Features:**
- Expandable form for creating custom topics
- Input fields:
  - **Topic Name**: Sanitized lowercase with underscores
  - **Description**: Detailed text area explaining the topic
  - **Examples**: Optional multi-line examples of logs/activities
- Direct function calls (no HTTP API calls)
- Success feedback with balloons animation
- Auto-refresh after successful creation

**"Show All Topics" Checkbox:**
- Displays all topics (predefined + custom)
- Separated into two sections: Custom Topics and Predefined Topics
- Shows descriptions in expandable cards
- Visual distinction: 🎨 for custom, 📚 for predefined

## How It Works

### Topic Creation Flow:
1. User fills out the form with topic name, description, and examples
2. UI calls `add_custom_topic_direct()` (direct function, no HTTP)
3. Backend validates inputs and checks uniqueness
4. Vector DB creates embedding from description + examples
5. Topic is added to topics collection with `custom: true` flag
6. In-memory topic embeddings cache is updated
7. Success message shown and UI refreshes

### Topic Matching (Automatic):
1. When logs are processed, embeddings are created for each log
2. Similarity scores are calculated against ALL topics (predefined + custom)
3. Custom topics participate in matching just like predefined topics
4. Top N matching topics are returned with similarity scores

### Integration Points:
- Custom topics are automatically included in:
  - Real-time log processing
  - Topic matching for local DDNA
  - Similarity score calculations
  - Topic-based restoration

## Architecture Benefits

✅ **Zero HTTP Latency**: Direct function calls from UI to backend
✅ **Persistent Storage**: Custom topics stored in ChromaDB
✅ **Auto-Integration**: Custom topics automatically participate in all topic matching
✅ **Cached Embeddings**: In-memory cache for fast similarity calculations
✅ **Dual Access**: Both direct functions and HTTP endpoints available

## Usage Example

### Creating a Custom Topic:
```python
# Via Direct Function (from UI or Python code)
result = add_custom_topic_direct(
    topic_name="game_development",
    description="Developing and playing video games. Includes game engines like Unity or Unreal, game design tools, Steam, gaming platforms, and game development tutorials.",
    examples=[
        "App: Unity.exe | Window: MyGame - Unity Editor",
        "Tab: Unreal Engine Documentation",
        "Tab: Steam Store"
    ]
)

# Via HTTP API (from external clients)
POST /api/topics/custom
{
    "topic_name": "game_development",
    "description": "Developing and playing video games...",
    "examples": [
        "App: Unity.exe | Window: MyGame - Unity Editor"
    ]
}
```

### Retrieving All Topics:
```python
# Via Direct Function
result = get_all_topics_direct()
# Returns: {status: "success", topics: {...}, total_count: 25}

# Via HTTP API
GET /api/topics/all
```

## File Changes Summary

### Modified Files:
1. **`backend/vector_db.py`**
   - Added `add_custom_topic()` method (110 lines)
   - Added `get_all_topics()` method (30 lines)

2. **`backend/server.py`**
   - Added `add_custom_topic_direct()` function (40 lines)
   - Added `get_all_topics_direct()` function (30 lines)
   - Added HTTP endpoints with Pydantic models (50 lines)

3. **`demo_ui/ui.py`**
   - Added custom topic function imports (15 lines)
   - Added "Create Custom Topic" UI section (95 lines)
   - Added "Show All Topics" display (40 lines)

### No Changes Required:
- Topic matching logic (already uses all topics from topics collection)
- Similarity calculation (already uses cached embeddings)
- Log processing pipeline (already includes custom topics automatically)

## Testing Checklist

- [ ] Create a new custom topic through UI
- [ ] Verify topic appears in "Show All Topics"
- [ ] Verify topic is marked as custom
- [ ] Check that logs match against the new custom topic
- [ ] Verify custom topics persist across server restarts
- [ ] Test duplicate topic name rejection
- [ ] Test form validation (empty name/description)
- [ ] Verify embeddings cache updates correctly

## Notes

- Topic names are automatically sanitized (lowercase, spaces→underscores)
- Custom topics are distinguished by `custom: "true"` in metadata
- The embedding model uses `all-MiniLM-L6-v2` (no external API needed)
- Custom topics are stored in ChromaDB's topics collection
- No restart required - topics are immediately available after creation
- The 60-second timeout is only for remote cloud sync, not for custom topics
