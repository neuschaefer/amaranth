from abc import abstractproperty

from ..hdl import *
from ..lib.cdc import ResetSynchronizer
from ..build import *


__all__ = ["QuicklogicPlatform"]


class QuicklogicPlatform(TemplatedPlatform):
    """
    Symbiflow toolchain
    -------------------

    Required tools:
        * ``symbiflow_synth``
        * ``symbiflow_pack``
        * ``symbiflow_place``
        * ``symbiflow_route``
        * ``symbiflow_write_fasm``
        * ``symbiflow_write_bitstream``

    The environment is populated by running the script specified in the environment variable
    ``NMIGEN_ENV_QLSymbiflow``, if present.

    Available overrides:
        * ``add_constraints``: inserts commands in XDC file.
    """

    device  = abstractproperty()
    package = abstractproperty()

    # Since the QuickLogic version of SymbiFlow toolchain is not upstreamed yet
    # we should distinguish the QuickLogic version from mainline one.
    # QuickLogic toolchain: https://github.com/QuickLogic-Corp/quicklogic-fpga-toolchain/releases
    toolchain = "QLSymbiflow"

    required_tools = [
        "symbiflow_synth",
        "symbiflow_pack",
        "symbiflow_place",
        "symbiflow_route",
        "symbiflow_write_fasm",
        "symbiflow_write_bitstream",
        "symbiflow_write_openocd",
    ]
    file_templates = {
        **TemplatedPlatform.build_script_templates,
        "{{name}}.v": r"""
            /* {{autogenerated}} */
            {{emit_verilog()}}
        """,
        "{{name}}.debug.v": r"""
            /* {{autogenerated}} */
            {{emit_debug_verilog()}}
        """,
        "{{name}}.pcf": r"""
            # {{autogenerated}}
            {% for port_name, pin_name, attrs in platform.iter_port_constraints_bits() -%}
                set_io {{port_name}} {{pin_name}}
            {% endfor %}
        """,
        "{{name}}.xdc": r"""
            # {{autogenerated}}
            {% for port_name, pin_name, attrs in platform.iter_port_constraints_bits() -%}
                {% for attr_name, attr_value in attrs.items() -%}
                    set_property {{attr_name}} {{attr_value}} [get_ports {{port_name|tcl_escape}} }]
                {% endfor %}
            {% endfor %}
            {{get_override("add_constraints")|default("# (add_constraints placeholder)")}}
        """,
        "{{name}}.sdc": r"""
            # {{autogenerated}}
            {% for net_signal, port_signal, frequency in platform.iter_clock_constraints() -%}
                {% if port_signal is not none -%}
                    create_clock -period {{100000000/frequency}} {{port_signal.name|ascii_escape}}
                {% endif %}
            {% endfor %}
        """
    }
    command_templates = [
        r"""
        {{invoke_tool("symbiflow_synth")}}
            -t {{name}}
            -v {% for file in platform.iter_files(".v", ".sv", ".vhd", ".vhdl") -%} {{file}} {% endfor %} {{name}}.v
            -d {{platform.device}}
            -p {{name}}.pcf
            -P {{platform.package}}
            -x {{name}}.xdc
        """,
        r"""
        {{invoke_tool("symbiflow_pack")}}
            -e {{name}}.eblif
            -d {{platform.device}}
            -s {{name}}.sdc
        """,
        r"""
        {{invoke_tool("symbiflow_place")}}
            -e {{name}}.eblif
            -d {{platform.device}}
            -p {{name}}.pcf
            -n {{name}}.net
            -P {{platform.package}}
            -s {{name}}.sdc
        """,
        r"""
        {{invoke_tool("symbiflow_route")}}
            -e {{name}}.eblif
            -d {{platform.device}}
            -s {{name}}.sdc
        """,
        r"""
        {{invoke_tool("symbiflow_write_fasm")}}
            -e {{name}}.eblif
            -d {{platform.device}}
            -s {{name}}.sdc
        """,
        r"""
        {{invoke_tool("symbiflow_write_bitstream")}}
            -f {{name}}.fasm
            -d {{platform.device}}
            -P {{platform.package}}
            -b {{name}}.bit
        """,
        # This should be `invoke_tool("symbiflow_write_openocd")`, but isn't because of a bug in
        # the QLSymbiflow v1.3.0 toolchain release.
        r"""
        python3 -m quicklogic_fasm.bitstream_to_openocd
            {{name}}.bit
            {{name}}.openocd
            --osc-freq {{platform.osc_freq}}
            --fpga-clk-divider {{platform.osc_div}}
        """,
    ]

    # Common logic

    @property
    def default_clk_constraint(self):
        if self.default_clk == "sys_clk0":
            return Clock(self.osc_freq / self.osc_div)
        return super().default_clk_constraint

    def add_clock_constraint(self, clock, frequency):
        super().add_clock_constraint(clock, frequency)
        clock.attrs["keep"] = "TRUE"

    def create_missing_domain(self, name):
        if name == "sync" and self.default_clk is not None:
            m = Module()
            if self.default_clk == "sys_clk0":
                if not hasattr(self, "osc_div"):
                    raise ValueError("OSC divider (osc_div) must be an integer between 2 "
                                     "and 512")
                if not isinstance(self.osc_div, int) or self.osc_div < 2 or self.osc_div > 512:
                    raise ValueError("OSC divider (osc_div) must be an integer between 2 "
                                     "and 512, not {!r}"
                                     .format(self.osc_div))
                if not hasattr(self, "osc_freq"):
                    raise ValueError("OSC frequency (osc_freq) must be an integer between 2100000 "
                                     "and 80000000")
                if not isinstance(self.osc_freq, int) or self.osc_freq < 2100000 or self.osc_freq > 80000000:
                    raise ValueError("OSC frequency (osc_freq) must be an integer between 2100000 "
                                     "and 80000000, not {!r}"
                                     .format(self.osc_freq))
                clk_i = Signal()
                sys_clk0 = Signal()
                m.submodules += Instance("qlal4s3b_cell_macro",
                                         o_Sys_Clk0=sys_clk0)
                m.submodules += Instance("gclkbuff",
                                         o_A=sys_clk0,
                                         o_Z=clk_i)
            else:
                clk_i = self.request(self.default_clk).i

            if self.default_rst is not None:
                rst_i = self.request(self.default_rst).i
            else:
                rst_i = Const(0)

            m.domains += ClockDomain("sync")
            m.d.comb += ClockSignal("sync").eq(clk_i)
            m.submodules.reset_sync = ResetSynchronizer(rst_i, domain="sync")
            return m
