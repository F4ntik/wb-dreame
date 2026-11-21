"""Shared SVG artwork in the coordinate space of each Dreame map."""
from math import ceil

DOCK_WIDTH_FRACTION = 0.08


def dock_padding(x, y, bounds, width):
    left, low, right, high = bounds
    distance_to_edge = min(x - left, y - low, right - x, high - y)
    return max(0, ceil(width / 2 - distance_to_edge))


def dock_svg(x, y, width):
    return (f'<g data-dock="true" role="img" aria-label="Зарядная станция" '
            f'transform="translate({x} {y}) scale({width / 24:.6f})">'
            '<rect x="-8.45" y="-11.05" width="16.9" height="22.1" rx="1.55" '
            'fill="#E9EBE6" stroke="#535D5A" stroke-width=".85"/>'
            '<path d="M-6.5-3v12.3m13-12.3V9.3" fill="none" stroke="#86918B" stroke-width=".9" stroke-linecap="round"/>'
            '<rect x="-3.1" y="-.05" width="6.2" height="2" rx=".55" fill="#D6DCD3" stroke="#B1BAB1" stroke-width=".55"/>'
            '<path d="M-3 .5v.9m6-.9v.9" fill="none" stroke="#A28B65" stroke-width=".8" stroke-linecap="round"/>'
            '<g><rect x="-8.45" y="-11.05" width="16.9" height="8.1" rx="1.55" '
            'fill="#FBFBF8" stroke="#535D5A" stroke-width=".85"/>'
            '<rect x="-6.9" y="-9.65" width="13.8" height="5.15" rx="1" '
            'fill="none" stroke="#AFB6AC" stroke-width=".62"/>'
            '<path d="M1.3-9.5v4.8" fill="none" stroke="#AAB2A8" stroke-width=".62"/>'
            '<path d="M-6.75-3.55h13.5" fill="none" stroke="#B39A70" stroke-width=".8" stroke-linecap="round"/></g></g>')
