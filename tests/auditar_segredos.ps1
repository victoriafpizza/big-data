$ErrorActionPreference = 'Stop'
$keys = @('POSTGRES_PASSWORD', 'AIRFLOW_ADMIN_PASSWORD', 'AIRFLOW_FERNET_KEY', 'AIRFLOW_WEB_SECRET_KEY')
$config = Get-Content -LiteralPath .env
$secretValues = foreach ($key in $keys) {
    $line = @($config | Where-Object { $_.StartsWith("$key=") })
    if ($line.Count -ne 1) { throw "Configure $key uma única vez em .env." }
    $value = $line[0].Substring($key.Length + 1)
    if ($value.Length -lt 16 -or $value.StartsWith('troque_por_')) {
        throw "Configure um valor local forte para $key."
    }
    $value
}
if (@($secretValues | Sort-Object -Unique).Count -ne $keys.Count) {
    throw 'Cada segredo deve ter seu próprio valor.'
}

$ignored = @('.env', '.env.local', 'secrets/local.pem', 'credentials/local.json',
    'airflow.db', 'logs/local.log', 'chegada/teste/metadados.json')
$ignoredResult = @(git check-ignore -- $ignored)
if ($LASTEXITCODE -ne 0 -or $ignoredResult.Count -ne $ignored.Count) {
    throw 'A proteção do Git está incompleta.'
}
if (git ls-files --cached -- .env '.env.*' ':!.env.example') {
    throw 'Arquivo de ambiente sensível no índice Git.'
}
"Git: $($ignored.Count) caminhos locais protegidos, .env real fora do índice."

function Assert-NoConfiguredSecret([string]$Text, [string]$Area) {
    foreach ($value in $secretValues) {
        if ($Text.Contains($value, [StringComparison]::Ordinal)) {
            throw "Valor configurado encontrado em $Area. O valor foi omitido."
        }
    }
}

$services = docker compose logs --no-color db init scheduler web 2>&1
if ($LASTEXITCODE) { throw 'Falha ao ler logs dos serviços.' }
Assert-NoConfiguredSecret ($services -join "`n") 'logs dos serviços'
$taskLogs = docker compose exec -T scheduler sh -c 'find /opt/airflow/logs -type f -name "attempt=*.log" -exec cat {} +' 2>&1
if ($LASTEXITCODE) { throw 'Falha ao ler logs das tarefas.' }
Assert-NoConfiguredSecret ($taskLogs -join "`n") 'logs das tarefas'
'Logs de serviço e de tarefas: nenhum dos quatro valores configurados encontrado.'

$runtime = @'
from airflow.configuration import conf
from airflow.models.crypto import get_fernet

assert conf.getboolean("core", "hide_sensitive_var_conn_fields")
assert get_fernet().is_encrypted
assert len(conf.get("webserver", "secret_key")) >= 32
print("mascaramento ativo; Fernet válido; chave web configurada")
'@
foreach ($service in @('scheduler', 'web')) {
    $runtime | docker compose exec -T $service python -
    if ($LASTEXITCODE) { throw "Verificação de configuração falhou em $service." }
}

$files = @(git ls-files --cached --others --exclude-standard -- .)
if ($LASTEXITCODE -or $files.Count -eq 0 -or $files -contains '.env') {
    throw 'Escopo inválido para o Gitleaks.'
}
$scratch = (New-Item -ItemType Directory -Force tmp).FullName
$target = (New-Item -ItemType Directory (Join-Path $scratch ("auditoria-" + [guid]::NewGuid()))).FullName
try {
    $snapshot = (New-Item -ItemType Directory (Join-Path $target 'snapshot')).FullName
    foreach ($file in $files) {
        $output = Join-Path $snapshot $file
        New-Item -ItemType Directory -Force (Split-Path -Parent $output) | Out-Null
        Copy-Item -LiteralPath $file -Destination $output
        if ([IO.Path]::GetExtension($file) -ne '.png') {
            Assert-NoConfiguredSecret (Get-Content -Raw -LiteralPath $file) 'arquivos elegíveis para Git'
        }
    }
    'Arquivos elegíveis para Git: nenhum dos quatro valores configurados encontrado.'
    docker run --rm --network none --mount "type=bind,source=$snapshot,target=/scan,readonly" `
        --mount "type=bind,source=$target,target=/out" zricethezav/gitleaks:v8.30.1 dir /scan `
        --redact=100 --no-banner --no-color --report-format json --report-path /out/report.json
    $scanExit = $LASTEXITCODE
    $report = Join-Path $target 'report.json'
    if ($scanExit -eq 0) { return }
    if ($scanExit -ne 1 -or -not (Test-Path -LiteralPath $report)) {
        throw 'O Gitleaks não concluiu a análise.'
    }

    # Linhas revisadas: quatro referências à URL pública do projeto e uma entrada sintética negativa.
    # Uma alteração no conteúdo exige nova revisão, mesmo no mesmo arquivo e número de linha.
    $reviewedLines = @{
        '.env.example' = 'CA84AD1F978DA9E97C029C834E6745492A9265E5FE41DBA145F433E5193054FC'
        'evidencias/arquivos-host.json' = 'F766B171B790ECD4BCC6A7AC54D336F3B281D7C4A2EA6EEA34EF9A1CFFD748BD'
        'README.md' = '22DC4F27A1FB77E7B101D680554FE6CB8248868E0CD9FB5259DCA0806AD9DBAA'
        'tests/test_ingestao.py' = @(
            '5D313AEE28B4EFC9B11983AAAF0A2E62B328E7858EB800B2FD7B42B7200F220D'
            'C02DC4E60A1D167FEAF69FC4194AD1FEBE56911748D1CE03F7F3C4326579BA7C'
        )
    }
    $findings = @(Get-Content -Raw -LiteralPath $report | ConvertFrom-Json)
    if ($findings.Count -eq 0) { throw 'O Gitleaks retornou erro sem ocorrências revisáveis.' }
    foreach ($finding in $findings) {
        $relative = $finding.File -replace '^/scan/', ''
        if ($finding.RuleID -ne 'generic-api-key' -or -not $reviewedLines.ContainsKey($relative) -or
                $finding.StartLine -ne $finding.EndLine) {
            throw "Ocorrência não revisada: $($finding.RuleID) ${relative}:$($finding.StartLine)"
        }
        $line = (Get-Content -LiteralPath (Join-Path $snapshot $relative))[$finding.StartLine - 1]
        $bytes = [Text.Encoding]::UTF8.GetBytes($line)
        $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
        if ($hash -notin $reviewedLines[$relative]) {
            throw "Conteúdo alterado em ${relative}:$($finding.StartLine). Revise o alerta."
        }
        "Falso positivo revisado: $($finding.RuleID) ${relative}:$($finding.StartLine)"
    }
    "Auditoria aprovada: $($findings.Count) alertas conhecidos, zero ocorrências não revisadas."
} finally {
    $resolvedTarget = (Resolve-Path -LiteralPath $target).Path
    if ((Split-Path -Parent $resolvedTarget) -eq (Resolve-Path -LiteralPath $scratch).Path) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse
    }
}


