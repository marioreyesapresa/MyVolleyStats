from django.contrib import admin
from .models import Equipo, Jugadora, Partido, RegistroEstadistica, RotacionSet

@admin.register(Equipo)
class EquipoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'temporada', 'entrenador')
    list_filter = ('entrenador', 'categoria')
    search_fields = ('nombre', 'categoria', 'temporada', 'entrenador__username')
    autocomplete_fields = ('entrenador',)

@admin.register(Jugadora)
class JugadoraAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'apellidos', 'dorsal', 'posicion', 'equipo')
    list_filter = ('equipo', 'posicion')
    search_fields = ('nombre', 'apellidos', 'dorsal')

@admin.register(Partido)
class PartidoAdmin(admin.ModelAdmin):
    list_display = ('rival', 'equipo', 'fecha', 'hora', 'local', 'modalidad')
    list_filter = ('equipo', 'local', 'modalidad')
    search_fields = ('rival', 'lugar')

@admin.register(RegistroEstadistica)
class RegistroEstadisticaAdmin(admin.ModelAdmin):
    list_display = ('partido', 'jugadora', 'set_numero', 'accion', 'calidad', 'rotacion_num')
    list_filter = ('partido', 'set_numero', 'accion', 'rotacion_num')

@admin.register(RotacionSet)
class RotacionSetAdmin(admin.ModelAdmin):
    list_display = ('partido', 'set_numero', 'es_inicial')
    list_filter = ('partido', 'set_numero')
