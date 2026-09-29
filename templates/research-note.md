---
title: "{{topic}}"
date: {{date:YYYY-MM-DD}}
tags: [research, {{topic_tags}}]
source: "web_search + CLAUDIO synthesis"
---

# {{topic}}

## 📋 Resumen Ejecutivo
{{summary}}

## 🔑 Hallazgos Clave
{% for finding in key_findings %}
- {{finding}}
{% endfor %}

## 📚 Fuentes
{% for source in sources %}
- [{{source.title}}]({{source.url}}) - {{source.date}}
{% endfor %}

## 💡 Ideas / Follow-up
- 

## 🏷️ Tags relacionados
{{related_tags}}