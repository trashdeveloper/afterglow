// tb_serv_cmp: REAL discriminant test for the SERV ALU compare mux.
//
// Fault (sim/targets/stranger3.yaml): the o_cmp select line
// `assign o_cmp = i_cmp_eq ? result_eq : result_lt;` with the two
// branches swapped (eq gets result_lt, lt gets result_eq).
// Quarantined from the serv-alu-lut stranger (BOOL_LUT rows): this TB
// never touches i_bool_op -- it drives i_cmp_eq with operand pairs
// where result_eq != result_lt, so every discriminating check flips
// under the swapped mux while the sanity rows pass under both values.
// With i_en=0/i_sub=0/i_cnt0=1/i_cmp_sig=0 the datapath is
// combinational-stable (add_cy_r pinned 0, cmp_r never latched), so the
// expectations below are exact single-bit arithmetic, not timing:
//   rs1=1,op_b=0: result_add=1 -> result_eq=0; rs1_sx=0,~op_b_sx=1 -> result_lt=1
//   rs1=0,op_b=1: result_add=1 -> result_eq=0; rs1_sx=0,~op_b_sx=1 -> result_lt=1
//   rs1=0,op_b=0: result_add=0 -> result_eq=1; result_lt=1 (sanity: same both ways)
// Prints "tohost: PASS" or "tohost: FAIL 0xdead" + "TRAP: ..." + "CYCLES=<n>".
// Deterministic. Accepts +seed=<n> (logged only). Verilator --binary --timing.
module tb_serv_cmp;
	reg clk = 0;
	always #5 clk = ~clk;

	reg i_en = 0;
	reg i_cnt0 = 1;
	reg i_sub = 0;
	reg [1:0] i_bool_op = 0;
	reg i_cmp_eq = 0;
	reg i_cmp_sig = 0;
	reg [3:0] i_rd_sel = 0;
	reg i_rs1 = 0;
	reg i_op_b = 0;
	reg i_buf = 0;
	wire o_cmp;
	wire o_rd;

	serv_alu dut (
		.clk(clk),
		.i_en(i_en),
		.i_cnt0(i_cnt0),
		.o_cmp(o_cmp),
		.i_sub(i_sub),
		.i_bool_op(i_bool_op),
		.i_cmp_eq(i_cmp_eq),
		.i_cmp_sig(i_cmp_sig),
		.i_rd_sel(i_rd_sel),
		.i_rs1(i_rs1),
		.i_op_b(i_op_b),
		.i_buf(i_buf),
		.o_rd(o_rd)
	);

	int unsigned cyc = 0;
	always @(posedge clk) cyc <= cyc + 1;

	int seed_val = 0;
	int fails = 0;

	task check(
		input eq,
		input a,
		input b,
		input exp
	);
		begin
			i_cmp_eq = eq;
			i_rs1 = a;
			i_op_b = b;
			repeat (2) @(posedge clk);
			#1;
			if (o_cmp !== exp) begin
				$display("TRAP: cmp mux mismatch eq=%b rs1=%b op_b=%b got=%b want=%b",
					eq, a, b, o_cmp, exp);
				fails = fails + 1;
			end else begin
				$display("- ok eq=%b rs1=%b op_b=%b cmp=%b", eq, a, b, o_cmp);
			end
		end
	endtask

	initial begin
		$value$plusargs("seed=%d", seed_val);
		$display("- tb_serv_cmp seed %0d", seed_val);
		$display("- serv cmp mux: eq selects result_eq");
		repeat (4) @(posedge clk);
		// Discriminating: result_eq != result_lt here, so the
		// swapped mux reads the wrong branch (want = pristine).
		check(1, 1, 0, 0);
		check(1, 0, 1, 0);
		check(0, 1, 0, 1);
		check(0, 0, 1, 1);
		// Sanity rows: eq and lt agree (both 1), pass under the
		// bug too -- prove the harness drives o_cmp.
		check(1, 0, 0, 1);
		check(0, 0, 0, 1);
		if (fails == 0) begin
			$display("tohost: PASS");
		end else begin
			$display("tohost: FAIL 0xdead");
			$display("TRAP: serv cmp mux %0d check(s) failed", fails);
		end
		$display("CYCLES=%0d", cyc);
		$finish;
	end
endmodule
