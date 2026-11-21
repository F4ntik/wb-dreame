"""Room outcomes explicitly reported in one Dreame map."""


def room_outcomes(frame, *, archived=False):
    def ids(values):
        if not isinstance(values, (list, dict)):
            return set()
        return {room for room in values if type(room) is int and 1 <= room <= 63}

    completed = ids(frame.completed_segments)
    skipped = ids(frame.skipped_segments)
    pending = ids(frame.pending_segments)
    blocked = ids(frame.blocked_segments) if archived else set()
    # The embedded CleanArea list is used for repeat cleaning, not proof of room completion.
    return {
        **{room: "completed" for room in completed - skipped - pending - blocked},
        **{room: "skipped" for room in skipped - completed - pending - blocked},
        **{room: "blocked" for room in blocked - completed - skipped - pending},
    }
