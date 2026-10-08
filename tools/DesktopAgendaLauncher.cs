using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Desktop Agenda")]
[assembly: AssemblyDescription("Portable launcher for Desktop Agenda")]
[assembly: AssemblyCompany("Desktop Agenda")]
[assembly: AssemblyProduct("Desktop Agenda")]
[assembly: AssemblyCopyright("Copyright (c) Desktop Agenda contributors")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

internal static class DesktopAgendaLauncher
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        string script = Path.Combine(root, "main.py");

        if (!File.Exists(python) || !File.Exists(script))
        {
            MessageBox.Show(
                "The application files are incomplete. Keep this executable next to main.py and the runtime folder.",
                "Desktop Agenda",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }

        try
        {
            var command = new StringBuilder();
            command.Append("-B ");
            command.Append(Quote(script));
            command.Append(" --shortcut");
            foreach (string arg in args)
            {
                command.Append(' ');
                command.Append(Quote(arg));
            }

            var start = new ProcessStartInfo
            {
                FileName = python,
                Arguments = command.ToString(),
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            Process.Start(start);
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(
                "Desktop Agenda could not be started.\n\n" + error.Message,
                "Desktop Agenda",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }
    }

    private static string Quote(string value)
    {
        if (value == null)
        {
            return "\"\"";
        }

        var result = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char character in value)
        {
            if (character == '\\')
            {
                backslashes++;
                continue;
            }

            if (character == '"')
            {
                result.Append('\\', backslashes * 2 + 1);
                result.Append('"');
                backslashes = 0;
                continue;
            }

            result.Append('\\', backslashes);
            backslashes = 0;
            result.Append(character);
        }
        result.Append('\\', backslashes * 2);
        result.Append('"');
        return result.ToString();
    }
}
