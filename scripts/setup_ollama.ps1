$log = "C:\Users\Admin\Documents\Default Project\JARVIS_Vault\data\logs\ollama_setup.log"
New-Item -ItemType Directory -Path (Split-Path $log) -Force | Out-Null
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Iniciando instalacion de Ollama..." | Out-File $log -Append -Encoding utf8
winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements *>> $log
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - winget exit code: $LASTEXITCODE" | Out-File $log -Append -Encoding utf8
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (Test-Path -LiteralPath $ollama) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Descargando modelo llama3.1:8b..." | Out-File $log -Append -Encoding utf8
    & $ollama pull llama3.1:8b *>> $log
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Descargando modelo nomic-embed-text..." | Out-File $log -Append -Encoding utf8
    & $ollama pull nomic-embed-text *>> $log
} else {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - ERROR: ollama.exe no encontrado tras instalacion" | Out-File $log -Append -Encoding utf8
}
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - FIN" | Out-File $log -Append -Encoding utf8
