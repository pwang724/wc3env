/* Integer conversion without overflowing the ticks * units intermediate. */
#pragma once
static long long clock_units(long long ticks, long long frequency, long long units) {
    return (ticks / frequency) * units + (ticks % frequency) * units / frequency;
}
