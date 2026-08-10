{{/*
Expand the name of the chart.
*/}}
{{- define "agent-framework.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncates at 63 chars because Kubernetes name field limit.
*/}}
{{- define "agent-framework.fullname" -}}
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
Create chart label value: "<chart-name>-<chart-version>"
*/}}
{{- define "agent-framework.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "agent-framework.labels" -}}
helm.sh/chart: {{ include "agent-framework.chart" . }}
{{ include "agent-framework.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — used by Deployments and Services for pod matching.
*/}}
{{- define "agent-framework.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agent-framework.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: {{ include "agent-framework.name" . }}
{{- end }}

{{/*
Name of the ConfigMap that holds catalog.yaml.
*/}}
{{- define "agent-framework.configmapName" -}}
{{- printf "%s-tool-catalog" (include "agent-framework.fullname" .) }}
{{- end }}
