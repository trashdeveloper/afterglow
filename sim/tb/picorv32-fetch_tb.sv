// tb_picorv32_fetch: REAL minimal harness for the picorv32 core.
// Small NOP memory, mem_ready tied high. Every cycle with mem_la_read it
// checks mem_la_addr[1:0]==0 (misaligned-fetch detector), watches the trap
// output, and requires PC progress (max fetch addr >= 64).
// Prints "tohost: PASS" or "tohost: FAIL 0xdead" + "TRAP: ..." + "CYCLES=<n>".
// Deterministic. Accepts +seed=<n> (logged only). Verilator --binary --timing.
module tb_picorv32_fetch;
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
		$display("- tb_picorv32_fetch seed %0d", seed_val);
		$display("- 256-word NOP mem, checking fetch alignment + trap + progress");
		for (i = 0; i < 256; i = i + 1)
			mem[i] = 32'h00000013;
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
		if (!misalign && !saw_trap && max_addr >= 64) begin
			$display("tohost: PASS");
		end else begin
			$display("tohost: FAIL 0xdead");
			if (misalign)
				$display("TRAP: fetch misalign addr low bits nonzero");
			else if (saw_trap)
				$display("TRAP: cpu trap asserted");
			else
				$display("TRAP: no progress max_addr=0x%08x", max_addr);
		end
		$display("CYCLES=%0d", cyc);
		$finish;
	end
endmodule
