shader_type canvas_item;

// A frosted veil: the window behind it, blurred (mipmaps of the screen) and dimmed toward the ground color, with a
// faint scan texture. `amount` fades it in and out; `blur` is 0 when effects are off.

uniform sampler2D screen_tex : hint_screen_texture, filter_linear_mipmap, repeat_disable;
uniform float amount : hint_range(0.0, 1.0) = 0.0;
uniform float blur : hint_range(0.0, 6.0) = 2.6;
uniform float crt : hint_range(0.0, 1.0) = 0.0;  // the outro: the picture squashed to a line, then a dot
uniform vec4 tint : source_color = vec4(0.02, 0.03, 0.06, 1.0);

void fragment() {
	if (crt > 0.0) {
		float sy = max(1.0 - crt * 1.3, 0.002);
		float sx = crt < 0.75 ? 1.0 : max(1.0 - (crt - 0.75) * 4.2, 0.002);
		vec2 d = SCREEN_UV - 0.5;
		vec2 suv = 0.5 + vec2(d.x / sx, d.y / sy);
		if (abs(suv.x - 0.5) > 0.5 || abs(suv.y - 0.5) > 0.5) {
			COLOR = vec4(0.0, 0.0, 0.0, 1.0);
		} else {
			vec3 c = texture(screen_tex, suv).rgb;
			float edge = 1.0 - smoothstep(0.3, 0.5, abs(suv.y - 0.5));  // the band's rim softens as it narrows
			c = mix(c, vec3(0.75, 0.97, 1.0), crt * 0.3) * (1.0 + crt * 1.6) * mix(1.0, edge, crt);
			COLOR = vec4(c, 1.0);
		}
	} else {
		vec4 sharp = texture(screen_tex, SCREEN_UV);
		vec4 soft = textureLod(screen_tex, SCREEN_UV, blur * amount);
		vec3 col = mix(sharp.rgb, soft.rgb, amount);
		col = mix(col, tint.rgb, 0.55 * amount);
		float scan = 0.5 + 0.5 * sin(SCREEN_UV.y * 900.0);
		col -= scan * 0.015 * amount;
		COLOR = vec4(col, 1.0);
	}
}
