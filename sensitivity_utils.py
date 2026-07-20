import hashlib
import os


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_config_entry(key, value, suffix=""):
    if suffix:
        return f"{key} {value}{suffix}"
    return f"{key} {value}"


def find_macro_value(macroname, command_name):
    with open(macroname, "r") as file:
        for line in file:
            stripped = line.strip()
            if stripped.startswith(command_name):
                return stripped[len(command_name):].strip()
    return "<missing>"


def should_keep_success_log(sample_name, log_mode, log_fraction, log_seed):
    if log_mode == "full":
        return True
    if log_mode in {"failures", "none"}:
        return False

    digest = hashlib.sha256(f"{log_seed}:{sample_name}".encode("ascii")).digest()
    sample_value = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return sample_value < log_fraction


def finalize_log_file(temp_log_file, final_log_file, keep_log):
    if keep_log:
        os.replace(temp_log_file, final_log_file)
    else:
        os.unlink(temp_log_file)


def replace_line(lines, start_string, new_line, allow_commented=False, append_if_missing=False, required=False):
    """Replace the first line in `lines` matching `start_string` (as a macro
    command, not just a text prefix). Optionally match commented-out lines,
    append if missing, or raise if the command is required but absent."""
    command = start_string.strip()

    def matches(candidate):
        if not candidate.startswith(command):
            return False
        return len(candidate) == len(command) or candidate[len(command)].isspace()

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("#") and matches(stripped):
            lines[i] = new_line + '\n'
            return lines

    if allow_commented:
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("#") and matches(stripped[1:].lstrip()):
                lines[i] = new_line + '\n'
                return lines

    if append_if_missing:
        lines.append(new_line + '\n')
    elif required:
        raise ValueError(f"Required command is missing from template: {command}")

    return lines