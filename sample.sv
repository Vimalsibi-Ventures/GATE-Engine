module clock_gating_test (
    input wire clk,
    input wire rst_n,
    input wire valid,
    input wire ready,
    input wire [7:0] data_in,
    output reg [7:0] data_out,
    output reg [1:0] state
);

    // Constants for FSM states
    localparam IDLE = 2'b00;
    localparam RUN  = 2'b01;
    localparam DONE = 2'b10;

    // FSM State Register
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
        end else begin
            case (state)
                IDLE: if (valid) state <= RUN;
                RUN:  if (ready) state <= DONE;
                DONE: state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

    // Gated Data Register
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_out <= 8'b0;
        end else begin
            // This is the clock-gating condition we want to analyze!
            if (valid && ready && (state == RUN)) begin
                data_out <= data_in;
            end
        end
    end

endmodule