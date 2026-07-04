with open('app/services/session_manager.py', 'r') as f:
    lines = f.readlines()

new_block = [
    '            else:\n',
    '                logger.warning(\n',
    '                    "get_opencode_session_info_no_valid_data",\n',
    '                    opencode_session_id=opencode_session_id,\n',
    '                )\n',
    '                return None\n',
]

lines[458:464] = new_block

with open('app/services/session_manager.py', 'w') as f:
    f.writelines(lines)

print('Fixed')