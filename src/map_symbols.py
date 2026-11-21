"""Shared SVG artwork in the coordinate space of each Dreame map."""
from math import ceil

DOCK_WIDTH_FRACTION = 0.065


def dock_padding(x, y, bounds, width):
    left, low, right, high = bounds
    distance_to_edge = min(x - left, y - low, right - x, high - y)
    return max(0, ceil(width / 2 - distance_to_edge))


def dock_svg(x, y, width):
    return (f'<g data-dock="true" transform="translate({x} {y}) scale({width / 24:.6f})">'
            '<path d="M-7 -10H7q2 0 2 2v8l2 7q.7 3-2.3 3H-8.7q-3 0-2.3-3l2-7v-8q0-2 2-2z" '
            'fill="#263E4A" stroke="#FFFFFF" stroke-width="1.6" stroke-linejoin="round"/>'
            '<rect x="-7" y="-8" width="14" height="6.5" rx="1.4" fill="#587685"/>'
            '<path d="M-2 -5h4" stroke="#E0F6EC" stroke-width="1.4" stroke-linecap="round"/>'
            '<path d="M-6.5 1h13l2 7h-17z" fill="#D9E8E7"/>'
            '<path d="M-4 3l-.8 3m9.6 0L4 3" fill="none" stroke="#698488" stroke-width="1.3" '
            'stroke-linecap="round"/></g>')
