<#
The local stand-in recognizer behind `python -m paul_vision.voice --listen`. Not meant to be run by hand.

Windows' own speech recognizer (System.Speech, part of .NET Framework, so it needs Windows PowerShell 5.1, not
PowerShell 7). Everything runs on this computer; nothing is sent anywhere.

stdin:  one line of JSON, the grammar from paul_vision.voice.listen_grammar:
        {"culture": "en-US", "wake": ["paul", ...], "rules": [[["find", "select"], ["", "the"], ["cup", ...]], ...],
         "decoy": true, "adaptation": false}
        Each rule is a sequence of slots, each slot a list of alternatives; "" makes the slot optional, and the
        slot "<dictation>" is free speech. With wake words, every phrase must start with one. decoy: also load free
        dictation as a competing grammar named "decoy". adaptation false: the recognizer does not adapt to the
        speaker.
stdout: one line of JSON per event:
        {"ready_ms": 412}                                    the grammar is loaded; listening starts
        {"text": "paul find the cup", "confidence": 0.93, "grammar": "commands"}
                                                             one recognition (with "wav" in loopback mode);
                                                             grammar "decoy" means outside the commands
        {"text": null, "wav": "..."}                         loopback: nothing recognized in that file
stderr: what went wrong, if it stops.

-WaveList <file>: loopback test. Recognize the wav files listed in the file (one path per line) in order, then exit,
instead of listening to the default microphone.
#>
param([string]$WaveList = '')

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Emit($object) {
    [Console]::Out.WriteLine(($object | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}

try {
    Add-Type -AssemblyName System.Speech
    $spec = [Console]::In.ReadLine() | ConvertFrom-Json
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $culture = New-Object System.Globalization.CultureInfo($spec.culture)
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
    if ($spec.adaptation -eq $false) { $engine.UpdateRecognizerSetting('AdaptationOn', 0) }

    $rules = New-Object System.Speech.Recognition.Choices
    foreach ($rule in $spec.rules) {
        $builder = New-Object System.Speech.Recognition.GrammarBuilder
        $builder.Culture = $culture
        foreach ($slot in $rule) {
            if ($slot -is [string] -and $slot -eq '<dictation>') { $builder.AppendDictation(); continue }
            $words = [string[]]@($slot | Where-Object { $_ -ne '' })
            $optional = @($slot | Where-Object { $_ -eq '' }).Count -gt 0
            $choices = New-Object System.Speech.Recognition.Choices(, $words)
            if ($optional) { $builder.Append($choices, 0, 1) } else { $builder.Append($choices) }
        }
        $rules.Add($builder)
    }
    $top = New-Object System.Speech.Recognition.GrammarBuilder
    $top.Culture = $culture
    $wake = [string[]]@($spec.wake | Where-Object { $_ -ne '' })
    if ($wake.Count -gt 0) { $top.Append((New-Object System.Speech.Recognition.Choices(, $wake))) }
    $top.Append($rules)
    $commands = New-Object System.Speech.Recognition.Grammar($top)
    $commands.Name = 'commands'
    $engine.LoadGrammar($commands)
    if ($spec.decoy) {
        # Free dictation competing with the commands: speech outside the grammar lands here instead of on the
        # nearest command. Its results are reported with grammar "decoy" and never sent.
        $decoy = New-Object System.Speech.Recognition.DictationGrammar
        $decoy.Name = 'decoy'
        $engine.LoadGrammar($decoy)
    }
    Emit @{ ready_ms = $watch.ElapsedMilliseconds }

    if ($WaveList) {
        foreach ($line in Get-Content -LiteralPath $WaveList) {
            $wav = [string]$line.Trim()   # a plain string: Get-Content's lines carry extra properties into the JSON
            if (-not $wav) { continue }
            $engine.SetInputToWaveFile($wav)
            $result = $engine.Recognize()
            if ($result) {
                Emit @{ wav = $wav; text = $result.Text; confidence = [math]::Round($result.Confidence, 3);
                        grammar = $result.Grammar.Name }
            } else {
                Emit @{ wav = $wav; text = $null }
            }
        }
        $engine.SetInputToNull()
    } else {
        $engine.SetInputToDefaultAudioDevice()
        while ($true) {
            $result = $engine.Recognize()   # returns null after a silence; then listen again
            if ($result) {
                Emit @{ text = $result.Text; confidence = [math]::Round($result.Confidence, 3);
                        grammar = $result.Grammar.Name }
            }
        }
    }
    $engine.Dispose()
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
