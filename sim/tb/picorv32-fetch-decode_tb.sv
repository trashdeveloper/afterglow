// tb_picorv32_fetch_decode: wave-5 hardening of tb_picorv32_fetch.
//
// Same REAL minimal harness for the picorv32 core (small boot ROM,
// mem_ready tied high) PLUS the eval/tb_kill_rate.md recommended
// decoder-exercising scenario: the boot stream opens with two chained
// ALU-immediate instructions with NONZERO immediates --
//   mem[0] = ADDI x5, x0, 123  (0x07B00293)
//   mem[1] = ADDI x6, x5, 1    (0x00128313, also probes rs1 forwarding)
// -- and the oracle asserts the RETIRED register values
// (x5 == 123, x6 == 124) at end of run.
//
// Why: the NOP-only boot (ADDI x0,x0,0 everywhere) never writes a
// nonzero register, so every writeback-path num_bump fault (narrowed
// cpuregs_wrdata, shifted decoded_imm, rerouted decoded_rd, ...) is
// oracle-invisible: 1211 num_bump equivalents escape here. One nonzero
// retired immediate moves that whole class into the oracle.
//
// Old oracle preserved: fetch-alignment check, trap watch, and PC
// progress (max fetch addr >= 64) are unchanged, with identical
// failure messages. New failure mode adds "TRAP: decode ..." lines.
// Pass-path displays are display-identical to tb_picorv32_fetch
// ("tohost: PASS" + "CYCLES=<n>" only; everything else is "- "-prefixed
// and checksum-excluded), so a pristine GREEN keeps the known fetch
// golden checksum and CYCLES=2009. Timing structure (10 + 2000
// cycles) is unchanged.
// Prints "tohost: PASS" or "tohost: FAIL 0xdead" + "TRAP: ..." + "CYCLES=<n>".
// Deterministic. Accepts +seed=<n> (logged only). Verilator --binary --timing.
module tb_picorv32_fetch_decode;
	reg clk = 0;
	reg resetn = 0;
	always #5 clk = ~clk;

	wire trap;
	wire mem_valid, mem_instr;
	wire mem_ready;
	wire [31:0] mem_addr, mem_wdata, mem_rdata;
	wire [3:0] mem_wstrb;
	wire mem_la_read, mem_la_write;
	wire [31:0] mem_la_addr;

	assign mem_ready = 1'b1;

	reg [31:0] mem [0:255];
	/* verilator lint_off WIDTH */
	assign mem_rdata = (mem_addr[31:10] == 0) ? mem[mem_addr[9:2]] : 32'h00000013;
	/* verilator lint_on WIDTH */

	picorv32 #(
		.COMPRESSED_ISA(0),
		.ENABLE_IRQ(0)
	) cpu (
		.clk(clk),
		.resetn(resetn),
		.trap(trap),
		.mem_valid(mem_valid),
		.mem_instr(mem_instr),
		.mem_ready(mem_ready),
		.mem_addr(mem_addr),
		.mem_wdata(mem_wdata),
		.mem_wstrb(mem_wstrb),
		.mem_rdata(mem_rdata),
		.mem_la_read(mem_la_read),
		.mem_la_write(mem_la_write),
		.mem_la_addr(mem_la_addr),
		.mem_la_wdata(),
		.mem_la_wstrb(),
		.pcpi_wr(1'b0),
		.pcpi_rd(32'b0),
		.pcpi_wait(1'b0),
		.pcpi_ready(1'b0),
		.irq(32'b0)
	);

	int unsigned cyc = 0;
	always @(posedge clk) cyc <= cyc + 1;

	integer i;
	int seed_val = 0;
	bit misalign = 0;
	bit saw_trap = 0;
	int unsigned max_addr = 0;

	initial begin
		$value$plusargs("seed=%d", seed_val);
		$display("- tb_picorv32_fetch_decode seed %0d", seed_val);
		$display("- 254-word NOP mem + 2xADDI up front, checking fetch alignment + trap + progress + retired immediates");
		for (i = 0; i < 256; i = i + 1)
			mem[i] = 32'h00000013;
		mem[0] = 32'h07B00293; // ADDI x5, x0, 123
		mem[1] = 32'h00128313; // ADDI x6, x5, 1 (x6 must retire 124)
		repeat (10) @(posedge clk);
		resetn = 1;
		repeat (2000) begin
			@(posedge clk);
			if (mem_la_read && mem_la_addr[1:0] != 2'b00)
				misalign = 1;
			if (mem_la_read && mem_la_addr > max_addr)
				max_addr = mem_la_addr;
			if (trap)
				saw_trap = 1;
		end
		$display("- retired: x5=0x%08x (want 0x0000007b) x6=0x%08x (want 0x0000007c)", cpu.cpuregs[5], cpu.cpuregs[6]);
		if (!misalign && !saw_trap && max_addr >= 64 && cpu.cpuregs[5] == 32'd123 && cpu.cpuregs[6] == 32'd124) begin
			$display("tohost: PASS");
		end else begin
			$display("tohost: FAIL 0xdead");
			if (misalign)
				$display("TRAP: fetch misalign addr low bits nonzero");
			else if (saw_trap)
				$display("TRAP: cpu trap asserted");
			else if (cpu.cpuregs[5] != 32'd123 || cpu.cpuregs[6] != 32'd124)
				$display("TRAP: decode retired-immediate mismatch x5=0x%08x x6=0x%08x", cpu.cpuregs[5], cpu.cpuregs[6]);
			else
				$display("TRAP: no progress max_addr=0x%08x", max_addr);
		end
		$display("CYCLES=%0d", cyc);
		$finish;
	end
endmodule
