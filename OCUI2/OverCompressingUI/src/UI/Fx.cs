using System;
using System.Collections.Generic;
using Godot;

namespace OverCompressingUI.UI;

/// <summary>What the engine adds that a toolkit does not have: the glow pass that makes HDR colors bloom, a neon
/// trail behind the mouse, a frosted veil that blurs the window behind a dialog, a scan sweep over a fresh listing, and small
/// synthesized sounds. All of it can be turned off (Settings: effects, sounds).</summary>
public static partial class Fx
{
    /// <summary>The glow pass over the whole window: everything brighter than 1.0 blooms. Needs 2D HDR
    /// (rendering/viewport/hdr_2d) and a Forward+ or Mobile renderer; on the OpenGL fallback it does nothing.</summary>
    public static WorldEnvironment Glow()
    {
        var env = new Godot.Environment
        {
            BackgroundMode = Godot.Environment.BGMode.Canvas,
            GlowEnabled = true,
            GlowIntensity = 0.9f,
            GlowStrength = 1.0f,
            GlowBloom = 0.0f,
            GlowBlendMode = Godot.Environment.GlowBlendModeEnum.Additive,
            GlowHdrThreshold = 1.0f,
            GlowHdrScale = 2.0f,
            GlowHdrLuminanceCap = 12f,
        };
        for (int i = 0; i < 7; i++)
            env.SetGlowLevel(i, i is 1 or 2 or 4 ? 1f : 0f);
        return new WorldEnvironment { Environment = env };
    }

    /// <summary>One soft spot of light (a radial falloff with no visible rings), for every glow drawn by hand.</summary>
    private static Texture2D? _spot;
    public static Texture2D Spot
    {
        get
        {
            if (_spot is null)
            {
                var grad = new Gradient
                {
                    Offsets = new[] { 0f, 0.18f, 0.42f, 0.7f, 1f },
                    Colors = new[] { new Color(1, 1, 1, 1), new Color(1, 1, 1, 0.72f), new Color(1, 1, 1, 0.32f), new Color(1, 1, 1, 0.08f), new Color(1, 1, 1, 0) },
                };
                _spot = new GradientTexture2D { Gradient = grad, Fill = GradientTexture2D.FillEnum.Radial, FillFrom = new Vector2(0.5f, 0.5f), FillTo = new Vector2(0.5f, 0f), Width = 96, Height = 96 };
            }
            return _spot;
        }
    }

    /// <summary>Draw a soft spot of light of diameter `size` at `at`.</summary>
    public static void DrawGlow(CanvasItem on, Vector2 at, float size, Color color)
        => on.DrawTextureRect(Spot, new Rect2(at - new Vector2(size, size) / 2, new Vector2(size, size)), false, color);

    /// <summary>A frosted veil over the window while a dialog or a job is in front: the window behind it blurred and
    /// dimmed, faded in and out. Counted, so several dialogs share one veil.</summary>
    public partial class Veil : ColorRect
    {
        private ShaderMaterial _mat = null!;
        private int _holds;
        private Tween? _tween;
        private float _amount;

        public override void _Ready()
        {
            SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
            MouseFilter = MouseFilterEnum.Stop;
            Visible = false;
            Color = Colors.White;
            _mat = new ShaderMaterial { Shader = GD.Load<Shader>("res://assets/shaders/veil.gdshader") };
            Material = _mat;
            _mat.SetShaderParameter("amount", 0f);
            _mat.SetShaderParameter("blur", Ui.Effects ? 2.6f : 0f);
        }

        public void Hold()
        {
            _holds++;
            if (_holds == 1)
                Fade(1f);
        }

        public void Release()
        {
            _holds = Math.Max(0, _holds - 1);
            if (_holds == 0)
                Fade(0f);
        }

        /// <summary>The outro: squash the picture to a line of light, then to a dot; done when it is dark.</summary>
        public System.Threading.Tasks.Task CrtOff(double seconds)
        {
            _tween?.Kill();
            Visible = true;
            var done = new System.Threading.Tasks.TaskCompletionSource<bool>();
            _tween = CreateTween();
            _tween.TweenMethod(Callable.From<float>(v => _mat.SetShaderParameter("crt", v)), 0f, 1f, seconds).SetTrans(Tween.TransitionType.Quad).SetEase(Tween.EaseType.In);
            _tween.TweenCallback(Callable.From(() => done.TrySetResult(true)));
            return done.Task;
        }

        private void Fade(float to)
        {
            _tween?.Kill();
            Visible = true;
            _mat.SetShaderParameter("blur", Ui.Effects ? 2.6f : 0f);
            _tween = CreateTween();
            _tween.TweenMethod(Callable.From<float>(v => { _amount = v; _mat.SetShaderParameter("amount", v); }), _amount, to, 0.22)
                .SetTrans(Tween.TransitionType.Sine).SetEase(Tween.EaseType.Out);
            if (to == 0f)
                _tween.TweenCallback(Callable.From(() => Visible = false));
        }
    }

    /// <summary>A line of light that sweeps down the list when it is listed afresh.</summary>
    public partial class Sweep : Control
    {
        private float _y = -1;
        private Tween? _tween;

        public override void _Ready()
        {
            SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
            MouseFilter = MouseFilterEnum.Ignore;
            ClipContents = true;
        }

        public void Run()
        {
            if (!Ui.Effects || Size.Y <= 0)
                return;
            _tween?.Kill();
            _tween = CreateTween();
            _tween.TweenMethod(Callable.From<float>(v => { _y = v; QueueRedraw(); }), 0f, Size.Y + 40f, 0.45).SetTrans(Tween.TransitionType.Quad).SetEase(Tween.EaseType.Out);
            _tween.TweenCallback(Callable.From(() => { _y = -1; QueueRedraw(); }));
        }

        public override void _Draw()
        {
            if (_y < 0)
                return;
            float h = Size.Y;
            var cyan = Ui.Neon;
            // the trail above the line
            for (int i = 1; i <= 10; i++)
            {
                float a = 0.07f * (1 - i / 11f);
                DrawRect(new Rect2(0, _y - i * 5, Size.X, 5), new Color(cyan, a));
            }
            float fade = Mathf.Clamp(1 - _y / (h + 40), 0, 1);
            DrawLine(new Vector2(0, _y), new Vector2(Size.X, _y), Ui.Hdr(cyan, 1.8f) with { A = 0.9f * fade }, 2);
        }
    }

    /// <summary>Small sounds, synthesized at start (no files): a tick under the mouse, a click, a two-note done, a
    /// low buzz for a failure, a rising blip for a dialog.</summary>
    public static class Sfx
    {
        private static readonly Dictionary<string, AudioStreamPlayer> Players = new();
        private static Node? _host;
        public static bool Enabled = true;
        private const int Rate = 22050;

        public static void Init(Node host)
        {
            _host = host;
            Add("tick", Tone(0.018, t => Mathf.Sin(t * Mathf.Tau * 2600) * Mathf.Exp(-t * 180)), -24);
            Add("click", Tone(0.05, t => Mathf.Sin(t * Mathf.Tau * (1500 - t * 9000)) * Mathf.Exp(-t * 70)), -16);
            Add("open", Tone(0.09, t => Mathf.Sin(t * Mathf.Tau * (520 + t * 6000)) * Mathf.Exp(-t * 30)), -18);
            Add("done", Tone(0.26, t => (t < 0.12 ? Mathf.Sin(t * Mathf.Tau * 660) : Mathf.Sin(t * Mathf.Tau * 990)) * Mathf.Exp(-(t % 0.12f) * 18)), -14);
            Add("error", Tone(0.22, t => (Mathf.Sin(t * Mathf.Tau * 110) > 0 ? 0.6f : -0.6f) * Mathf.Exp(-t * 9)), -16);
            // the intro: a low hum rising over half a second; the outro: a tone falling away
            Add("boot", Tone(0.6, t => (Mathf.Sin(Mathf.Tau * (90 * t + 300 * t * t)) * 0.7f + Mathf.Sin(Mathf.Tau * (180 * t + 600 * t * t)) * 0.3f) * Mathf.Min(t * 8, 1) * Mathf.Exp(-t * 2.2f)), -18);
            Add("off", Tone(0.4, t => Mathf.Sin(Mathf.Tau * (520 * t - 560 * t * t)) * Mathf.Exp(-t * 5)), -16);
        }

        private static AudioStreamWav Tone(double seconds, Func<float, float> f)
        {
            int n = (int)(Rate * seconds);
            var data = new byte[n * 2];
            for (int i = 0; i < n; i++)
            {
                float t = i / (float)Rate;
                float env = Mathf.Min(1f, i / 40f) * Mathf.Min(1f, (n - i) / 40f);  // no clicks at the ends
                short s = (short)(Mathf.Clamp(f(t) * env, -1f, 1f) * 32000);
                data[i * 2] = (byte)(s & 0xff);
                data[i * 2 + 1] = (byte)((s >> 8) & 0xff);
            }
            return new AudioStreamWav { Format = AudioStreamWav.FormatEnum.Format16Bits, MixRate = Rate, Stereo = false, Data = data };
        }

        private static void Add(string name, AudioStreamWav stream, float db)
        {
            var p = new AudioStreamPlayer { Stream = stream, VolumeDb = db, Name = "sfx_" + name };
            _host!.AddChild(p);
            Players[name] = p;
        }

        private static void Play(string name)
        {
            if (Enabled && Players.TryGetValue(name, out var p))
                p.Play();
        }

        public static void Tick() => Play("tick");
        public static void Click() => Play("click");
        public static void Open() => Play("open");
        public static void Done() => Play("done");
        public static void Error() => Play("error");
        public static void Boot() => Play("boot");
        public static void Off() => Play("off");
    }
}
