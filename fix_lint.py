import re

with open('app/services/session_manager.py', 'r') as f:
    content = f.read()

# Fix line 420
content = content.replace(
    'logger.info("get_opencode_session_info_output", opencode_session_id=opencode_session_id, stdout=output[:500], stderr=stderr_str[:500])',
    'logger.info(\n                "get_opencode_session_info_output",\n                opencode_session_id=opencode_session_id,\n                stdout=output[:500],\n                stderr=stderr_str[:500],\n            )'
)

# Fix line 441
content = content.replace(
    'logger.info("get_opencode_session_info_parsed", opencode_session_id=opencode_session_id, result=result)',
    'logger.info(\n                "get_opencode_session_info_parsed",\n                opencode_session_id=opencode_session_id,\n                result=result,\n            )'
)

# Fix line 444
content = content.replace(
    'logger.warning("get_opencode_session_info_json_error", opencode_session_id=opencode_session_id, error=str(e))',
    'logger.warning(\n                "get_opencode_session_info_json_error",\n                opencode_session_id=opencode_session_id,\n                error=str(e),\n            )'
)

# Fix line 446
content = content.replace(
    'logger.warning("get_opencode_session_info_no_valid_data", opencode_session_id=opencode_session_id)',
    'logger.warning(\n                "get_opencode_session_info_no_valid_data",\n                opencode_session_id=opencode_session_id,\n            )'
)

with open('app/services/session_manager.py', 'w') as f:
    f.write(content)

print('Fixed')