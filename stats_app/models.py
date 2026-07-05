from django.db import models

class Equipo(models.Model):
    nombre = models.CharField(max_length=100, verbose_name="Nombre del Equipo")
    temporada = models.CharField(max_length=50, verbose_name="Temporada (ej. 2025/2026)")
    CATEGORIAS_CHOICES = [
        ('BENJAMIN', 'Benjamín'),
        ('ALEVIN', 'Alevín'),
        ('INFANTIL', 'Infantil'),
        ('CADETE', 'Cadete'),
        ('JUVENIL', 'Juvenil'),
        ('JUNIOR', 'Junior'),
        ('SENIOR', 'Senior'),
    ]
    categoria = models.CharField(max_length=20, choices=CATEGORIAS_CHOICES, default='SENIOR', verbose_name="Categoría")
    entrenador_principal = models.CharField(max_length=100, blank=True, null=True, verbose_name="Entrenador Principal")

    def __str__(self):
        return f"{self.nombre} ({self.temporada})"

    class Meta:
        verbose_name = "Equipo"
        verbose_name_plural = "Equipos"

class Jugadora(models.Model):
    POSICIONES = [
        ('COLOCADORA', 'Colocadora'),
        ('OPUESTA', 'Opuesta'),
        ('CENTRAL', 'Central'),
        ('RECEPTORA', 'Receptora'),
        ('LIBERO', 'Líbero'),
    ]
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE, related_name='jugadoras', verbose_name="Equipo")
    dorsal = models.PositiveIntegerField(blank=True, null=True, verbose_name="Dorsal")
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    apellidos = models.CharField(max_length=150, verbose_name="Apellidos")
    posicion = models.CharField(max_length=20, choices=POSICIONES, blank=True, null=True, verbose_name="Posición")
    fecha_nacimiento = models.DateField(verbose_name="Fecha de Nacimiento", blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} {self.apellidos} - Dorsal: {self.dorsal if self.dorsal else 'N/A'}"

    class Meta:
        verbose_name = "Jugadora"
        verbose_name_plural = "Jugadoras"

class Partido(models.Model):
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE, related_name='partidos', verbose_name="Equipo")
    fecha = models.DateField(verbose_name="Fecha del Partido")
    hora = models.TimeField(verbose_name="Hora del Partido")
    rival = models.CharField(max_length=150, verbose_name="Rival")
    local = models.BooleanField(default=True, verbose_name="¿Juega como Local?")
    lugar = models.CharField(max_length=200, verbose_name="Lugar/Pabellón")
    MODALIDADES = [
        ('VOLEY', 'Voleibol 6x6'),
        ('MINIVOLEY', 'Minivoley 4x4'),
    ]
    modalidad = models.CharField(max_length=15, choices=MODALIDADES, default='VOLEY', verbose_name="Modalidad")
    finalizado = models.BooleanField(default=False, verbose_name="Finalizado")

    # ── Configuración de reglas del set (editable desde Ajustes) ───────────
    puntos_por_set = models.PositiveIntegerField(default=25, verbose_name="Puntos por set")
    puntos_set_decisivo = models.PositiveIntegerField(default=15, verbose_name="Puntos del set decisivo")
    sets_para_ganar = models.PositiveIntegerField(default=3, verbose_name="Sets para ganar el partido")

    def __str__(self):
        return f"{self.equipo.nombre} vs {self.rival} ({self.fecha} {self.hora})"

    @property
    def set_decisivo_numero(self):
        """Número de set que se juega con el límite de puntos reducido (p.ej. 5º en un mejor de 5 sets)."""
        return (self.sets_para_ganar * 2) - 1

    def limite_puntos_set(self, set_numero):
        return self.puntos_set_decisivo if int(set_numero) == self.set_decisivo_numero else self.puntos_por_set

    class Meta:
        verbose_name = "Partido"
        verbose_name_plural = "Partidos"
        ordering = ['-fecha', '-hora']

class RegistroEstadistica(models.Model):
    ACCIONES = [
        ('SAQUE', 'Saque'),
        ('RECEPCION', 'Recepción'),
        ('COLOCACION', 'Colocación'),
        ('ATAQUE', 'Ataque'),
        ('BLOQUEO', 'Bloqueo'),
        ('DEFENSA', 'Defensa'),
        ('ERROR_RIVAL', 'Error del Rival'),
        ('PUNTO_RIVAL', 'Punto del Rival'),
        ('SUSTITUCION', 'Sustitución'),
    ]
    CALIDADES = [
        ('++', '++'),
        ('+', '+'),
        ('=', '='),
        ('-', '-'),
        ('--', '--'),
    ]
    FASES = [
        ('K1', 'K1 (Recepción/Ataque)'),
        ('K2', 'K2 (Saque/Defensa)'),
    ]

    partido = models.ForeignKey(Partido, on_delete=models.CASCADE, related_name='estadisticas')
    jugadora = models.ForeignKey(Jugadora, on_delete=models.CASCADE, related_name='estadisticas', null=True, blank=True)
    set_numero = models.PositiveIntegerField(default=1)
    tipo_fase = models.CharField(max_length=5, choices=FASES)
    accion = models.CharField(max_length=20, choices=ACCIONES)
    calidad = models.CharField(max_length=2, choices=CALIDADES, blank=True, null=True)
    rotacion_num = models.PositiveIntegerField(default=1, verbose_name="Rotación Activa")
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.partido} - {self.jugadora} - {self.accion}"

    class Meta:
        verbose_name = "Registro de Estadística"
        verbose_name_plural = "Registros de Estadísticas"

class RotacionSet(models.Model):
    partido = models.ForeignKey(Partido, on_delete=models.CASCADE, related_name='rotaciones')
    set_numero = models.PositiveIntegerField(default=1)
    
    pos1 = models.ForeignKey(Jugadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos1_rotaciones', verbose_name="Zona 1 (Saque)")
    pos2 = models.ForeignKey(Jugadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos2_rotaciones', verbose_name="Zona 2")
    pos3 = models.ForeignKey(Jugadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos3_rotaciones', verbose_name="Zona 3")
    pos4 = models.ForeignKey(Jugadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos4_rotaciones', verbose_name="Zona 4")
    pos5 = models.ForeignKey(Jugadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos5_rotaciones', verbose_name="Zona 5")
    pos6 = models.ForeignKey(Jugadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos6_rotaciones', verbose_name="Zona 6")
    libero1 = models.ForeignKey(Jugadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='libero1_rotaciones', verbose_name="Líbero 1")
    libero2 = models.ForeignKey(Jugadora, on_delete=models.SET_NULL, null=True, blank=True, related_name='libero2_rotaciones', verbose_name="Líbero 2")

    es_inicial = models.BooleanField(default=False)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        tipo = "Inicial" if self.es_inicial else "Actual"
        return f"{self.partido} - Set {self.set_numero} ({tipo})"

    class Meta:
        verbose_name = "Rotación de Set"
        verbose_name_plural = "Rotaciones de Sets"
