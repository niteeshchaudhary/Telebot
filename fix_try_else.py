with open('app/services/session_manager.py', 'r') as f:
    content = f.read()

old = '''            # Parse export output (single JSON object with "info" field)
            try:
                data = json.loads(output.strip())
                # Export format has "info" field with session metadata
                if isinstance(data, dict) and "info" in data:
                    info = data["info"]
                    if isinstance(info, dict):
                        # Normalize the info to match expected format
                        result = {
                            "id": info.get("id", opencode_session_id),
                            "title": info.get("title", ""),
                            "directory": info.get("directory", ""),
                            "projectID": info.get("projectID", ""),
                            "path": info.get("path", ""),
                            "agent": info.get("agent", ""),
                            "model": info.get("model", {}),
                            "created": info.get("created", ""),
                            "updated": info.get("updated", ""),
                        }
                        logger.info(
                            "get_opencode_session_info_parsed",
                            opencode_session_id=opencode_session_id,
                            result=result,
                        )
                        return result
            except json.JSONDecodeError as e:
                logger.warning(
                    "get_opencode_session_info_json_error",
                    opencode_session_id=opencode_session_id,
                    error=str(e),
                )

            logger.warning(
                "get_opencode_session_info_no_valid_data",
                opencode_session_id=opencode_session_id,
            )
            return None
        except Exception as e:
            logger.exception("get_opencode_session_info_failed", error=str(e))
            return None'''

new = '''            # Parse export output (single JSON object with "info" field)
            try:
                data = json.loads(output.strip())
                # Export format has "info" field with session metadata
                if isinstance(data, dict) and "info" in data:
                    info = data["info"]
                    if isinstance(info, dict):
                        # Normalize the info to match expected format
                        result = {
                            "id": info.get("id", opencode_session_id),
                            "title": info.get("title", ""),
                            "directory": info.get("directory", ""),
                            "projectID": info.get("projectID", ""),
                            "path": info.get("path", ""),
                            "agent": info.get("agent", ""),
                            "model": info.get("model", {}),
                            "created": info.get("created", ""),
                            "updated": info.get("updated", ""),
                        }
                        logger.info(
                            "get_opencode_session_info_parsed",
                            opencode_session_id=opencode_session_id,
                            result=result,
                        )
                        return result
            except json.JSONDecodeError as e:
                logger.warning(
                    "get_opencode_session_info_json_error",
                    opencode_session_id=opencode_session_id,
                    error=str(e),
                )
            else:
                logger.warning(
                    "get_opencode_session_info_no_valid_data",
                    opencode_session_id=opencode_session_id,
                )
                return None
        except Exception as e:
            logger.exception("get_opencode_session_info_failed", error=str(e))
            return None'''

if old in content:
    content = content.replace(old, new)
    with open('app/services/session_manager.py', 'w') as f:
        f.write(content)
    print('Replaced successfully')
else:
    print('OLD NOT FOUND')