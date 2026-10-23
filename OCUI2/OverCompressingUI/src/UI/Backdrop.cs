using Godot;

namespace OverCompressingUI.UI;

/// <summary>The substrate (assets/shaders/backdrop.gdshader) under the circuit board (Circuit). Its slow light
/// advances only while the window has the focus, at 30 steps a second, so an idle window costs nothing.</summary>
public partial class Backdrop : ColorRect
{
    private ShaderMaterial _material = null!;
    private double _time;
    private double _accum;
    private bool _focused = true;
    public bool Animated = true;

    public override void _Ready()
    {
        var shader = GD.Load<Shader>("res://assets/shaders/backdrop.gdshader");
        _material = new ShaderMaterial { Shader = shader };
        Material = _material;
        Color = Ui.Bg;
        MouseFilter = MouseFilterEnum.Ignore;
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        GetWindow().FocusEntered += () => _focused = true;
        GetWindow().FocusExited += () => _focused = false;
        _material.SetShaderParameter("cell_px", 34f * Ui.Scale);
        SetIntensity(Ui.Effects ? 1f : 0.5f);
    }

    public void SetIntensity(float v) => _material.SetShaderParameter("intensity", v);

    public override void _Process(double delta)
    {
        if (!_focused || !Animated)
            return;
        _accum += delta;
        if (_accum < 1.0 / 30)
            return;
        _time += _accum;
        _accum = 0;
        _material.SetShaderParameter("time", (float)_time);
    }
}
