"""As regras de cálculo da jornada — o que a CLT dá como padrão e o que a
convenção ou o acordo coletivo da empresa pode mudar.

Cada conjunto de regras tem vigência (data de início) e nunca é editado: uma
mudança é um conjunto novo, com nova vigência. Assim o espelho de março de
2027 é recalculado, anos depois, com as regras que valiam em março de 2027.

O sistema calcula e alerta; quem confere se as regras batem com a convenção
coletiva é o contador ou o advogado da empresa.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class Regras:
    # Tolerância (CLT art. 58, § 1º; Súmula 366 do TST): variações de até 5 min
    # por marcação, no máximo 10 min no dia, não contam. Passou disso, conta tudo.
    tolerancia_por_marcacao: int = 5
    tolerancia_diaria: int = 10
    # Adicionais (CF art. 7º, XVI: no mínimo 50%).
    adicional_extra: int = 50
    adicional_descanso_feriado: int = 100
    limite_extra_diario: int = 120  # CLT art. 59: até 2 h por dia
    # Trabalho noturno urbano (CLT art. 73): 22h às 5h, hora de 52min30s, 20%.
    inicio_noturno: str = "2200"
    fim_noturno: str = "0500"
    hora_noturna_reduzida: bool = True
    adicional_noturno: int = 20
    # Súmula 60, II, do TST: jornada que começa no período noturno e passa das
    # 5h continua noturna depois das 5h.
    prorrogacao_noturna: bool = True
    # Intervalo intrajornada (CLT art. 71).
    intervalo_minimo_acima_de_6h: int = 60
    intervalo_minimo_de_4_a_6h: int = 15
    intervalo_maximo: int = 120
    # TST, IRR Tema 14: redução eventual de até 5 min no total (início + fim do
    # intervalo), por pequenas variações de marcação, não gera o art. 71, § 4º.
    tolerancia_intervalo: int = 5
    # Interjornada (CLT art. 66): 11 h entre o fim de uma jornada e o início da próxima.
    interjornada_minima: int = 660
    # Duas marcações separadas por mais que isto são jornadas diferentes.
    separador_de_jornadas: int = 360
    # Banco de horas (CLT art. 59, §§ 2º, 5º e 6º). 0 = sem banco.
    banco_de_horas_meses: int = 0
    banco_inclui_descanso_feriado: bool = False
    # Ponto britânico (Súmula 338, III): alerta depois de N dias seguidos com
    # todas as marcações idênticas ao horário contratual.
    alerta_britanico_dias: int = 5

    def __post_init__(self) -> None:
        for h in (self.inicio_noturno, self.fim_noturno):
            if len(h) != 4 or not h.isdigit() or int(h[:2]) > 23 or int(h[2:]) > 59:
                raise ValueError(f"horário inválido: {h}")
        if self.adicional_extra < 50:
            raise ValueError("adicional de hora extra abaixo de 50% fere a Constituição (art. 7º, XVI)")
        if self.adicional_noturno < 20:
            raise ValueError("adicional noturno abaixo de 20% fere a CLT (art. 73)")
        if self.banco_de_horas_meses not in (0, 1, 6, 12):
            raise ValueError("banco de horas: 0 (sem), 1 (mensal), 6 (acordo individual) ou 12 (acordo coletivo)")
        for nome in ("tolerancia_por_marcacao", "tolerancia_diaria", "limite_extra_diario", "intervalo_minimo_acima_de_6h",
                     "intervalo_minimo_de_4_a_6h", "intervalo_maximo", "tolerancia_intervalo", "interjornada_minima", "separador_de_jornadas"):
            if getattr(self, nome) < 0:
                raise ValueError(f"{nome} não pode ser negativo")

    @classmethod
    def de_dict(cls, d: dict | None) -> "Regras":
        conhecidos = {f.name for f in fields(cls)}
        desconhecidos = set(d or {}) - conhecidos
        if desconhecidos:
            raise ValueError(f"regras desconhecidas: {', '.join(sorted(desconhecidos))}")
        return cls(**(d or {}))

    def como_dict(self) -> dict:
        return asdict(self)
