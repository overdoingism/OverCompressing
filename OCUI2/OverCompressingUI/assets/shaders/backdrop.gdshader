shader_type canvas_item;

// The substrate under the circuit board (Circuit.cs draws the traces over this): near-black, a faint grid at the
// board's cell size, a slow breath of light, scanlines, vignette and a trace of grain.

uniform float time = 0.0;
uniform float intensity : hint_range(0.0, 1.0) = 1.0;
uniform float cell_px = 34.0;
uniform vec4 base_color : source_color = vec4(0.018, 0.024, 0.05, 1.0);
uniform vec4 grid_color : source_color = vec4(0.0, 0.94, 1.0, 1.0);
uniform vec4 breath_color : source_color = vec4(0.54, 0.36, 1.0, 1.0);

float hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

void fragment() {
	vec2 uv = UV;
	vec2 px = FRAGCOORD.xy;
	vec3 col = base_color.rgb;

	// the grid of the board
	vec2 g = abs(fract(px / cell_px) - 0.5);
	float line = 1.0 - smoothstep(0.0, 1.5 / cell_px, min(g.x, g.y));
	col += grid_color.rgb * line * 0.035 * intensity;
	// every 4th line a little stronger
	vec2 g4 = abs(fract(px / (cell_px * 4.0)) - 0.5);
	float line4 = 1.0 - smoothstep(0.0, 1.5 / (cell_px * 4.0), min(g4.x, g4.y));
	col += grid_color.rgb * line4 * 0.03 * intensity;

	// a slow breath of violet light wandering over the board
	float t = time * 0.04;
	vec2 c = vec2(0.5 + 0.35 * sin(t * 1.3), 0.5 + 0.3 * cos(t * 0.9));
	col += breath_color.rgb * smoothstep(0.75, 0.0, distance(vec2(uv.x * 1.6, uv.y), vec2(c.x * 1.6, c.y))) * 0.07 * intensity;

	// scanlines, vignette, grain
	col *= 1.0 - 0.05 * (0.5 + 0.5 * sin(px.y * 3.1416)) * intensity;
	col *= 0.78 + 0.22 * smoothstep(1.3, 0.3, distance(uv, vec2(0.5)));
	col += (hash(uv * 1024.0 + fract(time)) - 0.5) * 0.012;
	COLOR = vec4(col, 1.0);
}
