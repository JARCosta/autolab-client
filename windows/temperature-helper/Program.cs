using System.Text.Json;
using System.Text.Json.Serialization;
using LibreHardwareMonitor.Hardware;

namespace AutolabNode.TemperatureHelper;

internal sealed record SensorReading(
    string HardwareName,
    string HardwareType,
    string SensorName,
    string SensorType,
    double Value);

internal sealed record TemperatureSnapshot(
    double? CpuTempC,
    SensorReading? Source);

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static int Main()
    {
        try
        {
            var snapshot = ReadSnapshot();
            Console.WriteLine(JsonSerializer.Serialize(snapshot, JsonOptions));
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(JsonSerializer.Serialize(new { error = ex.Message }, JsonOptions));
            return 1;
        }
    }

    private static TemperatureSnapshot ReadSnapshot()
    {
        var readings = new List<SensorReading>();
        var computer = new Computer
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
            IsMemoryEnabled = true,
            IsMotherboardEnabled = true,
            IsControllerEnabled = true,
            IsNetworkEnabled = true,
            IsStorageEnabled = true,
        };

        try
        {
            computer.Open();
            computer.Accept(new UpdateVisitor());

            foreach (var hardware in computer.Hardware)
            {
                CollectHardware(hardware, readings);
            }
        }
        finally
        {
            computer.Close();
        }

        var chosen = ChooseCpuTemperature(readings);
        return new TemperatureSnapshot(chosen?.Value, chosen);
    }

    private static void CollectHardware(IHardware hardware, List<SensorReading> readings)
    {
        var hardwareName = hardware.Name ?? string.Empty;
        var hardwareType = hardware.HardwareType.ToString();

        foreach (var sensor in hardware.Sensors)
        {
            if (sensor.SensorType != SensorType.Temperature)
            {
                continue;
            }

            if (sensor.Value is not float value)
            {
                continue;
            }

            readings.Add(new SensorReading(
                hardwareName,
                hardwareType,
                sensor.Name ?? string.Empty,
                sensor.SensorType.ToString(),
                value));
        }

        foreach (var subHardware in hardware.SubHardware)
        {
            CollectHardware(subHardware, readings);
        }
    }

    private static SensorReading? ChooseCpuTemperature(IReadOnlyList<SensorReading> readings)
    {
        var cpuCandidates = readings.Where(IsCpuCandidate).ToList();
        var candidates = cpuCandidates.Count > 0 ? cpuCandidates : readings.ToList();

        if (candidates.Count == 0)
        {
            return null;
        }

        return candidates
            .Where(r => r.Value is > -30 and < 150)
            .OrderByDescending(ScoreCandidate)
            .ThenByDescending(r => r.Value)
            .FirstOrDefault();
    }

    private static bool IsCpuCandidate(SensorReading reading)
    {
        return Contains(reading.HardwareType, "cpu")
            || Contains(reading.HardwareName, "cpu")
            || Contains(reading.HardwareName, "processor")
            || Contains(reading.SensorName, "cpu")
            || Contains(reading.SensorName, "package")
            || Contains(reading.SensorName, "tctl")
            || Contains(reading.SensorName, "tdie")
            || Contains(reading.SensorName, "core max")
            || Contains(reading.SensorName, "die average")
            || Contains(reading.SensorName, "core");
    }

    private static int ScoreCandidate(SensorReading reading)
    {
        var score = 0;

        if (Contains(reading.HardwareType, "cpu"))
        {
            score += 120;
        }

        if (Contains(reading.HardwareName, "cpu"))
        {
            score += 100;
        }

        if (Contains(reading.HardwareName, "processor"))
        {
            score += 80;
        }

        if (Contains(reading.SensorName, "cpu package"))
        {
            score += 70;
        }

        if (Contains(reading.SensorName, "package"))
        {
            score += 45;
        }

        if (Contains(reading.SensorName, "tctl"))
        {
            score += 40;
        }

        if (Contains(reading.SensorName, "tdie"))
        {
            score += 40;
        }

        if (Contains(reading.SensorName, "core max"))
        {
            score += 35;
        }

        if (Contains(reading.SensorName, "die average"))
        {
            score += 35;
        }

        if (Contains(reading.SensorName, "core"))
        {
            score += 10;
        }

        if (Contains(reading.HardwareName, "gpu") || Contains(reading.SensorName, "gpu"))
        {
            score -= 150;
        }

        if (Contains(reading.HardwareName, "motherboard"))
        {
            score -= 20;
        }

        return score;
    }

    private static bool Contains(string value, string needle)
    {
        return value.Contains(needle, StringComparison.OrdinalIgnoreCase);
    }

    private sealed class UpdateVisitor : IVisitor
    {
        public void VisitComputer(IComputer computer) => computer.Traverse(this);

        public void VisitHardware(IHardware hardware)
        {
            hardware.Update();
            foreach (var subHardware in hardware.SubHardware)
            {
                subHardware.Accept(this);
            }
        }

        public void VisitSensor(ISensor sensor)
        {
        }

        public void VisitParameter(IParameter parameter)
        {
        }
    }
}