{{- define "rdst.name" -}}
{{- default "rdst" .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "rdst.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default "rdst" .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "rdst.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "rdst.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "rdst.selectorLabels" -}}
app.kubernetes.io/name: {{ include "rdst.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "rdst.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "rdst.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "rdst.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- $repo := ternary .Values.image.repository (printf "%s/%s" .Values.image.registry .Values.image.repository) (empty .Values.image.registry) -}}
{{- if .Values.image.digest -}}
{{ $repo }}@{{ .Values.image.digest }}
{{- else -}}
{{ $repo }}:{{ $tag }}
{{- end -}}
{{- end }}

{{- define "rdst.authSecretName" -}}
{{- default (printf "%s-auth" (include "rdst.fullname" .)) .Values.auth.existingSecret }}
{{- end }}

{{- define "rdst.authSecretKey" -}}
{{- ternary .Values.auth.existingSecretKey "access-token" (not (empty .Values.auth.existingSecret)) }}
{{- end }}

{{- define "rdst.publicUrl" -}}
{{- if .Values.publicUrl }}
{{- .Values.publicUrl | trimSuffix "/" }}
{{- else if and .Values.ingress.enabled .Values.ingress.hosts }}
{{- $host := (index .Values.ingress.hosts 0).host }}
{{- printf "%s://%s" (ternary "https" "http" (not (empty .Values.ingress.tls))) $host }}
{{- end }}
{{- end }}

{{/* The environment variable a target's password arrives in: RDST_<NAME>_PASSWORD. */}}
{{- define "rdst.targetPasswordEnv" -}}
{{- printf "RDST_%s_PASSWORD" (regexReplaceAll "[^A-Z0-9]+" (upper (required "each target needs a name" .name)) "_" | trimAll "_") }}
{{- end }}

{{/* The targets list for RDST_SEED_TARGETS: connection details only, never a password. */}}
{{- define "rdst.seedTargets" -}}
{{- $out := list }}
{{- range .Values.targets }}
{{- $t := omit . "passwordSecret" }}
{{- if .passwordSecret }}
{{- $t = set $t "passwordEnv" (include "rdst.targetPasswordEnv" .) }}
{{- end }}
{{- $out = append $out $t }}
{{- end }}
{{- toJson $out }}
{{- end }}
