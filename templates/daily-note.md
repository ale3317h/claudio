---
date: {{date:YYYY-MM-DD}}
day: {{date:dddd}}
tags: [daily-note]
---

# {{date:YYYY-MM-DD}} {{date:dddd}}

## 🎯 Objetivos del día
- [ ] 

## 📅 Eventos
```dataview
LIST FROM "daily-notes" WHERE date = date(today) SORT time
```

## 💬 Conversaciones con CLAUDIO
```dataview
LIST FROM "daily-notes" WHERE contains(file.name, "claudio-log") SORT file.ctime DESC LIMIT 10
```

## 📝 Notas rápidas
-

## 🔍 Investigación del día
```dataview
LIST FROM "research" WHERE date(created) = date(today)
```

---
*Generado automáticamente por CLAUDIO*