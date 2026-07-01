{{/*
Expand the name of the chart.
*/}}
{{- define "inference-ollama.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncate at 63 characters because some Kubernetes name fields are limited to this length.
If the release name already contains the chart name, use only the release name.
*/}}
{{- define "inference-ollama.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label value.
*/}}
{{- define "inference-ollama.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "inference-ollama.labels" -}}
helm.sh/chart: {{ include "inference-ollama.chart" . }}
{{ include "inference-ollama.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for Ollama pods.
*/}}
{{- define "inference-ollama.selectorLabels" -}}
app.kubernetes.io/name: inference-ollama
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Full labels for Inference Adapter pods (includes chart + version + managed-by).
*/}}
{{- define "inference-ollama.adapterLabels" -}}
helm.sh/chart: {{ include "inference-ollama.chart" . }}
{{ include "inference-ollama.adapterSelectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for Inference Adapter pods.
*/}}
{{- define "inference-ollama.adapterSelectorLabels" -}}
app.kubernetes.io/name: inference-adapter
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
