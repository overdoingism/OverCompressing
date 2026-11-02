using System;
using System.Collections.Generic;
using System.Linq;

namespace OverCompressingUI.Core;

/// <summary>How a model's weights are kept, read from its manifest's tensor tables (owner's classes, 2026-11-02):
/// by the nominal bits of the dtype most of its weights (numbers, not bytes: a small model's embedding, kept at
/// 8 bits, takes most of a Q4 file's bytes) are in. 16 bits and up may be the original numbers;
/// 6 to 15 is near-lossless (Q8, Q6, FP8); 4 to 5 is a low loss (Q4, Q5); under 4 is the lossy compromise (Q3, Q2,
/// IQ*, TQ*); what cannot be told is "other" (no weights, a .bin/.pt without a tensor table, an unknown type).</summary>
public enum WeightClass { Lossless, NearLossless, LowLoss, Lossy, Other }

/// <summary>A model's weights, summed up: its class, its container (safetensors, gguf, other), and how many of its
/// weights are in each dtype (by name, e.g. Q4_K: 83%).</summary>
public sealed record WeightMix(WeightClass Class, string Container, Dictionary<string, long> BytesByType, long WeightBytes, float? Bits)
{
    public static readonly WeightMix None = new(WeightClass.Other, "", new Dictionary<string, long>(), 0, null);

    /// <summary>The dtypes by their share, largest first, as "Q4_K 83%, Q6_K 12%, F32 5%".</summary>
    public string Shares(int most = 4)
    {
        if (WeightBytes <= 0)
            return "";
        var parts = BytesByType.OrderByDescending(kv => kv.Value).Take(most)
            .Select(kv => $"{kv.Key} {Math.Round(100.0 * kv.Value / WeightBytes)}%").ToList();
        if (BytesByType.Count > most)
            parts.Add("…");
        return string.Join(", ", parts);
    }
}

public static class Weights
{
    /// <summary>The GGUF tensor types by number (ggml_type), as the tool writes them into a manifest for the ones it
    /// does not name: name and nominal bits per weight.</summary>
    private static readonly Dictionary<int, (string name, float bits)> Ggml = new()
    {
        [0] = ("F32", 32), [1] = ("F16", 16), [2] = ("Q4_0", 4), [3] = ("Q4_1", 4), [6] = ("Q5_0", 5), [7] = ("Q5_1", 5),
        [8] = ("Q8_0", 8), [9] = ("Q8_1", 8), [10] = ("Q2_K", 2), [11] = ("Q3_K", 3), [12] = ("Q4_K", 4), [13] = ("Q5_K", 5),
        [14] = ("Q6_K", 6), [15] = ("Q8_K", 8), [16] = ("IQ2_XXS", 2), [17] = ("IQ2_XS", 2), [18] = ("IQ3_XXS", 3),
        [19] = ("IQ1_S", 1), [20] = ("IQ4_NL", 4), [21] = ("IQ3_S", 3), [22] = ("IQ2_S", 2), [23] = ("IQ4_XS", 4),
        [24] = ("I8", 8), [25] = ("I16", 16), [26] = ("I32", 32), [27] = ("I64", 64), [28] = ("F64", 64), [29] = ("IQ1_M", 1),
        [30] = ("BF16", 16), [34] = ("TQ1_0", 1), [35] = ("TQ2_0", 2), [36] = ("MXFP4", 4),
    };

    /// <summary>A dtype as the manifest writes it, shown by name, with its nominal bits (null: unknown). A packed
    /// GPTQ/AWQ weight (an I32 tensor named qweight) holds 4-bit numbers.</summary>
    public static (string name, float? bits) Type(string dtype, string tensorName)
    {
        if (dtype.StartsWith("ggml_type_") && int.TryParse(dtype["ggml_type_".Length..], out var id))
            return Ggml.TryGetValue(id, out var g) ? g : (dtype, null);
        var up = dtype.ToUpperInvariant();
        if (up is "I32" or "U32" && tensorName.EndsWith("qweight", StringComparison.OrdinalIgnoreCase))
            return ("INT4 (packed)", 4);
        return up switch
        {
            "F64" or "FLOAT64" or "I64" or "U64" => (up, 64),
            "F32" or "FLOAT32" or "I32" or "U32" => (up, 32),
            "F16" or "BF16" or "FLOAT16" or "BFLOAT16" or "I16" or "U16" => (up, 16),
            "F8_E4M3" or "F8_E5M2" or "FP8" or "I8" or "U8" or "BOOL" => (up, 8),
            _ => Ggml.Values.FirstOrDefault(g => g.name == up) is { name.Length: > 0 } known ? known : (dtype, null),
        };
    }

    public static WeightClass ClassOf(float? bits) => bits switch
    {
        null => WeightClass.Other,
        >= 16 => WeightClass.Lossless,
        >= 6 => WeightClass.NearLossless,
        >= 4 => WeightClass.LowLoss,
        _ => WeightClass.Lossy,
    };

    /// <summary>Sum a model's weights up from its manifest: the class most of its weights are in, its container,
    /// and the weights by dtype (counted as numbers, by each tensor's shape; by bytes where a table has no shape).
    /// A weight file without a tensor table (.bin, .pt) counts whole, by its bytes, as unknown.</summary>
    public static WeightMix Of(PackReader.Manifest manifest)
    {
        var bytes = new Dictionary<string, long>();
        var bitsOf = new Dictionary<string, float?>();
        var byClass = new Dictionary<WeightClass, long>();
        var containers = new Dictionary<string, long>();
        long total = 0;
        foreach (var f in manifest.Files)
        {
            bool weights = f.Tensors.Count > 0 || Place.KindOf(f.Path) == "weights";
            if (!weights)
                continue;
            var container = f.Format is "safetensors" or "gguf" ? f.Format : "other";
            containers[container] = containers.GetValueOrDefault(container) + f.Size;
            if (f.Tensors.Count == 0)
            {
                bytes["?"] = bytes.GetValueOrDefault("?") + f.Size;
                byClass[WeightClass.Other] = byClass.GetValueOrDefault(WeightClass.Other) + f.Size;
                total += f.Size;
                continue;
            }
            foreach (var t in f.Tensors)
            {
                var (name, bits) = Type(t.Dtype, t.Name);
                long count = t.Elements > 1 ? t.Elements : t.Bytes;
                bytes[name] = bytes.GetValueOrDefault(name) + count;
                bitsOf[name] = bits;
                var c = ClassOf(bits);
                byClass[c] = byClass.GetValueOrDefault(c) + count;
                total += count;
            }
        }
        if (total <= 0)
            return WeightMix.None;
        var cls = byClass.OrderByDescending(kv => kv.Value).First().Key;
        var cont = containers.OrderByDescending(kv => kv.Value).First().Key;
        // the shade within the class: the bits of the dtype most of the class's weights are in
        var leading = bytes.Where(kv => bitsOf.TryGetValue(kv.Key, out var b) && ClassOf(b) == cls).OrderByDescending(kv => kv.Value).FirstOrDefault();
        float? dominant = leading.Key is not null && bitsOf.TryGetValue(leading.Key, out var lb) ? lb : null;
        return new WeightMix(cls, cont, bytes, total, dominant);
    }

    public static string Label(WeightClass c) => T.Get("wclass_" + c switch
    {
        WeightClass.Lossless => "lossless", WeightClass.NearLossless => "near", WeightClass.LowLoss => "low", WeightClass.Lossy => "lossy", _ => "other",
    });
}
